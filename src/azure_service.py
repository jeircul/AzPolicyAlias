from typing import List, Dict, Any, Optional
import logging
import os
from datetime import datetime, timedelta
from azure.identity import (
    DefaultAzureCredential,
    AzureCliCredential,
    ManagedIdentityCredential,
    ChainedTokenCredential,
)
from azure.mgmt.resource import ResourceManagementClient
from azure.core.exceptions import (
    AzureError,
    ClientAuthenticationError,
    HttpResponseError,
    ServiceRequestError,
)
from azure.core.pipeline.policies import RetryPolicy
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

logger = logging.getLogger(__name__)


class RetryWithBackoff:
    """Exponential backoff retry helper"""

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay

    async def execute(self, func, *args, **kwargs):
        """Execute function with exponential backoff retry"""
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except (ServiceRequestError, HttpResponseError) as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.base_delay * (2**attempt)
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"All {self.max_retries} attempts failed")
            except ClientAuthenticationError as e:
                # Don't retry auth errors
                logger.error(f"Authentication error: {e}")
                raise
            except Exception as e:
                last_exception = e
                logger.error(f"Unexpected error: {e}")
                raise

        raise last_exception if last_exception else Exception("Retry failed")


class AzurePolicyService:
    def __init__(self, subscription_id: str, cache_duration_hours: int = 1):
        self.subscription_id = subscription_id
        self.client: Optional[ResourceManagementClient] = None
        self.cache: Dict[str, Any] = {}
        self.cache_timestamp: Optional[datetime] = None
        self.cache_duration = timedelta(hours=cache_duration_hours)
        self.retry_helper = RetryWithBackoff(max_retries=3, base_delay=1.0)
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._setup_client()

    def _setup_client(self):
        """Setup Azure client with robust authentication chain"""
        try:
            # Get client_id from environment for UAMI (required for non-AKS Kubernetes)
            client_id = os.getenv("AZURE_CLIENT_ID")
            
            # Create a chained credential with multiple fallback options
            credentials = [AzureCliCredential()]
            
            # Add ManagedIdentityCredential with client_id if available (for UAMI)
            if client_id:
                logger.info(f"Using ManagedIdentityCredential with client_id: {client_id[:8]}...")
                credentials.append(ManagedIdentityCredential(client_id=client_id))
            else:
                # Fallback to system-assigned identity
                logger.info("Using ManagedIdentityCredential (system-assigned)")
                credentials.append(ManagedIdentityCredential())
            
            # Add DefaultAzureCredential as final fallback
            credentials.append(DefaultAzureCredential())
            
            credential = ChainedTokenCredential(*credentials)

            # Test the credential
            try:
                credential.get_token("https://management.azure.com/.default")
                logger.info("Successfully authenticated with Azure")
            except Exception as e:
                logger.warning(f"Initial credential test failed: {e}")

            # Create client with custom retry policy
            self.client = ResourceManagementClient(
                credential, self.subscription_id, logging_enable=True
            )

        except Exception as e:
            logger.error(f"Failed to setup Azure client: {e}")
            raise

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid"""
        if not self.cache_timestamp or not self.cache:
            return False
        return datetime.now() - self.cache_timestamp < self.cache_duration

    async def get_policy_aliases(
        self, force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        """Get all policy aliases with caching and retry logic"""
        if not force_refresh and self._is_cache_valid():
            logger.info(
                f"Returning cached policy aliases ({len(self.cache.get('aliases', []))} items)"
            )
            return self.cache.get("aliases", [])

        logger.info("Fetching policy aliases from Azure API")

        # Use retry helper with async execution
        async def fetch_with_retry():
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(self._executor, self._fetch_aliases_sync)

        try:
            aliases = await self.retry_helper.execute(fetch_with_retry)

            # Update cache
            self.cache["aliases"] = aliases
            self.cache_timestamp = datetime.now()

            logger.info(f"Successfully cached {len(aliases)} policy aliases")
            return aliases

        except Exception as e:
            logger.error(f"Failed to fetch policy aliases: {e}")
            # Return stale cache if available
            if self.cache.get("aliases"):
                logger.warning("Returning stale cache due to fetch failure")
                return self.cache["aliases"]
            raise

    def _fetch_aliases_sync(self) -> List[Dict[str, Any]]:
        """Synchronous method to fetch aliases from Azure with error handling"""
        if not self.client:
            raise ValueError("Azure client not initialized")

        try:
            start_time = time.time()

            # First get list of all provider namespaces
            providers_list = list(self.client.providers.list())
            fetch_time = time.time() - start_time

            logger.info(
                f"Fetched {len(providers_list)} provider namespaces in {fetch_time:.2f}s"
            )

            all_aliases = []
            providers_with_aliases = 0
            failed_providers = []

            # Process providers in parallel batches
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import threading

            lock = threading.Lock()

            # Azure ARM API limits:
            # - 12,000 reads per hour per subscription (200/min)
            # - We have ~312 providers, so we can safely use 20-30 workers
            # - This keeps us well under the rate limit while maximizing speed
            max_workers = 25

            def fetch_provider_aliases(provider_summary):
                """Fetch aliases for a single provider"""
                namespace = provider_summary.namespace
                if not namespace:
                    return []

                try:
                    provider = self.client.providers.get(
                        namespace, expand="resourceTypes/aliases"
                    )

                    if not provider.resource_types:
                        return []

                    aliases = []
                    for resource_type in provider.resource_types:
                        if not resource_type.aliases:
                            continue

                        for alias in resource_type.aliases:
                            # Extract default_pattern as dict if it exists
                            default_pattern = None
                            if (
                                hasattr(alias, "default_pattern")
                                and alias.default_pattern
                            ):
                                pattern_obj = alias.default_pattern
                                default_pattern = {
                                    "phrase": getattr(pattern_obj, "phrase", None),
                                    "variable": getattr(pattern_obj, "variable", None),
                                    "type": getattr(pattern_obj, "type", None),
                                }

                            alias_data = {
                                "namespace": namespace,
                                "resource_type": resource_type.resource_type,
                                "alias_name": alias.name,
                                "default_path": alias.default_path,
                                "default_pattern": default_pattern,
                                "type": getattr(alias, "type", None),
                            }
                            aliases.append(alias_data)

                    return aliases

                except Exception as e:
                    logger.warning(f"Failed to fetch aliases for {namespace}: {e}")
                    with lock:
                        failed_providers.append(namespace)
                    return []

            # Process all providers in parallel (no batching needed at this scale)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_provider = {
                    executor.submit(fetch_provider_aliases, provider): provider
                    for provider in providers_list
                }

                completed = 0
                for future in as_completed(future_to_provider):
                    aliases = future.result()
                    if aliases:
                        with lock:
                            providers_with_aliases += 1
                            all_aliases.extend(aliases)

                    completed += 1
                    # Log progress every 100 providers
                    if completed % 100 == 0:
                        elapsed = time.time() - start_time
                        logger.info(
                            f"Progress: {completed}/{len(providers_list)} providers, "
                            f"{len(all_aliases)} aliases found ({elapsed:.1f}s)"
                        )

            total_time = time.time() - start_time
            logger.info(f"Providers with aliases: {providers_with_aliases}")
            if failed_providers:
                logger.warning(
                    f"Failed to fetch {len(failed_providers)} providers: {', '.join(failed_providers[:5])}{'...' if len(failed_providers) > 5 else ''}"
                )
            logger.info(
                f"Successfully processed {len(all_aliases)} policy aliases "
                f"from {len(providers_list)} providers in {total_time:.2f}s"
            )
            return all_aliases

        except AzureError as e:
            logger.error(f"Azure API error: {type(e).__name__}: {e}")
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error fetching policy aliases: {type(e).__name__}: {e}"
            )
            raise

    async def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics about policy aliases"""
        aliases = await self.get_policy_aliases()

        namespaces = set()
        resource_types = set()
        types_by_namespace: Dict[str, int] = {}

        for alias in aliases:
            ns = alias["namespace"]
            namespaces.add(ns)
            resource_types.add(f"{ns}/{alias['resource_type']}")
            types_by_namespace[ns] = types_by_namespace.get(ns, 0) + 1

        return {
            "total_aliases": len(aliases),
            "total_namespaces": len(namespaces),
            "total_resource_types": len(resource_types),
            "cache_age_seconds": (
                int((datetime.now() - self.cache_timestamp).total_seconds())
                if self.cache_timestamp
                else None
            ),
            "cache_valid": self._is_cache_valid(),
            "top_namespaces": sorted(
                types_by_namespace.items(), key=lambda x: x[1], reverse=True
            )[:10],
        }

    async def search_aliases(
        self, query: str, namespace_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search aliases with improved filtering logic"""
        aliases = await self.get_policy_aliases()

        if not query and not namespace_filter:
            return aliases

        filtered_aliases = []
        query_lower = query.lower() if query else ""
        query_terms = query_lower.split() if query_lower else []

        for alias in aliases:
            # Apply namespace filter if provided
            if namespace_filter and alias["namespace"] != namespace_filter:
                continue

            # Apply text search if query provided
            if query_terms:
                searchable_text = " ".join(
                    [
                        alias["namespace"],
                        alias["resource_type"],
                        alias["alias_name"],
                        alias["default_path"] or "",
                    ]
                ).lower()

                # All terms must match (AND logic)
                if not all(term in searchable_text for term in query_terms):
                    continue

            filtered_aliases.append(alias)

        return filtered_aliases

    async def get_namespaces_with_counts(self) -> List[Dict[str, Any]]:
        """Get namespaces with alias counts"""
        aliases = await self.get_policy_aliases()
        namespace_counts: Dict[str, int] = {}

        for alias in aliases:
            ns = alias["namespace"]
            namespace_counts[ns] = namespace_counts.get(ns, 0) + 1

        return [
            {"namespace": ns, "count": count}
            for ns, count in sorted(
                namespace_counts.items(), key=lambda x: (-x[1], x[0])
            )
        ]

    def __del__(self):
        """Cleanup executor on deletion"""
        if hasattr(self, "_executor"):
            self._executor.shutdown(wait=False)
