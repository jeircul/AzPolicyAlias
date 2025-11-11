// Application state
let allAliases = [];
let filteredAliases = [];
let currentPage = 1;
const itemsPerPage = 100; // Increased from 50
let sortColumn = null;
let sortDirection = 'asc';

// DOM elements
const elements = {
    loading: document.getElementById('loading'),
    error: document.getElementById('error'),
    searchInput: document.getElementById('search-input'),
    namespaceFilter: document.getElementById('namespace-filter'),
    refreshBtn: document.getElementById('refresh-btn'),
    clearFiltersBtn: document.getElementById('clear-filters-btn'),
    exportBtn: document.getElementById('export-btn'),
    resultsInfo: document.getElementById('results-info'),
    resultsCount: document.getElementById('results-count'),
    queryTime: document.getElementById('query-time'),
    tableBody: document.getElementById('table-body'),
    pagination: document.getElementById('pagination'),
    pageInfo: document.getElementById('page-info'),
    prevBtn: document.getElementById('prev-btn'),
    nextBtn: document.getElementById('next-btn'),
    cacheInfo: document.getElementById('cache-info'),
    stats: {
        totalAliases: document.getElementById('total-aliases'),
        totalNamespaces: document.getElementById('total-namespaces'),
        totalResourceTypes: document.getElementById('total-resource-types')
    }
};

// Utility functions
function showElement(element) {
    element?.classList.remove('hidden');
}

function hideElement(element) {
    element?.classList.add('hidden');
}

function showError(message) {
    const errorMessage = elements.error.querySelector('.error-message');
    if (errorMessage) {
        errorMessage.textContent = message;
    }
    showElement(elements.error);
    hideElement(elements.loading);
}

function hideError() {
    hideElement(elements.error);
}

function showLoading() {
    showElement(elements.loading);
    hideElement(elements.error);
}

function hideLoading() {
    hideElement(elements.loading);
}

function formatCacheAge(seconds) {
    if (!seconds) return '';
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m ago`;
}

// API functions
async function fetchWithErrorHandling(url, options = {}) {
    try {
        const response = await fetch(url, options);
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(errorData.detail || `HTTP ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`API Error (${url}):`, error);
        throw error;
    }
}

async function loadStatistics() {
    try {
        const stats = await fetchWithErrorHandling('/api/statistics');
        elements.stats.totalAliases.textContent = stats.total_aliases.toLocaleString();
        elements.stats.totalNamespaces.textContent = stats.total_namespaces.toLocaleString();
        elements.stats.totalResourceTypes.textContent = stats.total_resource_types.toLocaleString();

        // Update cache info
        if (stats.cache_age_seconds !== null) {
            const cacheAgeText = formatCacheAge(stats.cache_age_seconds);
            elements.cacheInfo.textContent = `Cache: ${cacheAgeText}`;
            if (!stats.cache_valid) {
                elements.cacheInfo.classList.add('stale');
            } else {
                elements.cacheInfo.classList.remove('stale');
            }
        }
    } catch (error) {
        console.error('Failed to load statistics:', error);
    }
}

async function loadNamespaces() {
    try {
        const data = await fetchWithErrorHandling('/api/namespaces?with_counts=true');
        const select = elements.namespaceFilter;

        // Clear existing options except the first one
        while (select.children.length > 1) {
            select.removeChild(select.lastChild);
        }

        // Add namespace options with counts if available, sorted alphabetically
        if (data.with_counts) {
            // Sort alphabetically by namespace
            const sortedNamespaces = data.with_counts.sort((a, b) =>
                a.namespace.localeCompare(b.namespace)
            );
            sortedNamespaces.forEach(ns => {
                const option = document.createElement('option');
                option.value = ns.namespace;
                option.textContent = `${ns.namespace} (${ns.count.toLocaleString()})`;
                select.appendChild(option);
            });
        } else {
            // Sort alphabetically
            const sortedNamespaces = [...data.namespaces].sort((a, b) =>
                a.localeCompare(b)
            );
            sortedNamespaces.forEach(namespace => {
                const option = document.createElement('option');
                option.value = namespace;
                option.textContent = namespace;
                select.appendChild(option);
            });
        }
    } catch (error) {
        console.error('Failed to load namespaces:', error);
        showError(`Failed to load namespaces: ${error.message}`);
    }
}

async function loadAliases(forceRefresh = false) {
    showLoading();
    hideError();

    try {
        const query = elements.searchInput.value.trim();
        const namespace = elements.namespaceFilter.value;

        const params = new URLSearchParams();
        if (query) params.append('query', query);
        if (namespace) params.append('namespace', namespace);
        if (forceRefresh) params.append('force_refresh', 'true');

        const data = await fetchWithErrorHandling(`/api/aliases?${params}`);

        allAliases = data.aliases;
        filteredAliases = allAliases;
        currentPage = 1;

        updateResultsInfo(data.query_time_ms);
        applySorting();
        renderTable();
        updatePagination();

        hideLoading();

        // Load statistics after successful data load
        await loadStatistics();

    } catch (error) {
        showError(`Failed to load aliases: ${error.message}`);
    }
}

// Sorting functionality
function applySorting() {
    if (!sortColumn) return;

    filteredAliases.sort((a, b) => {
        let aVal = a[sortColumn] || '';
        let bVal = b[sortColumn] || '';

        // Handle null/undefined
        if (!aVal) return 1;
        if (!bVal) return -1;

        // String comparison
        const comparison = String(aVal).localeCompare(String(bVal));
        return sortDirection === 'asc' ? comparison : -comparison;
    });
}

function setSortColumn(column) {
    const headers = document.querySelectorAll('th.sortable');

    if (sortColumn === column) {
        // Toggle direction
        sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        sortColumn = column;
        sortDirection = 'asc';
    }

    // Update header indicators
    headers.forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.column === column) {
            th.classList.add(sortDirection === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    });

    applySorting();
    currentPage = 1;
    renderTable();
    updatePagination();
}

// Export functionality
function exportToCSV() {
    if (filteredAliases.length === 0) {
        showError('No data to export');
        setTimeout(hideError, 3000);
        return;
    }

    // Create CSV content
    const headers = ['Namespace', 'Resource Type', 'Alias Name', 'Default Path'];
    const rows = filteredAliases.map(alias => [
        alias.namespace,
        alias.resource_type,
        alias.alias_name,
        alias.default_path || ''
    ]);

    const csvContent = [
        headers.join(','),
        ...rows.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
    ].join('\n');

    // Create blob and download
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').substring(0, 19);

    link.setAttribute('href', url);
    link.setAttribute('download', `azure-policy-aliases-${timestamp}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

// Table rendering
function renderTable() {
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const pageData = filteredAliases.slice(startIndex, endIndex);

    if (pageData.length === 0) {
        elements.tableBody.innerHTML = `
            <tr role="row">
                <td colspan="4" style="text-align: center; padding: 2rem; color: var(--fg-secondary);">
                    No aliases found matching your criteria
                </td>
            </tr>
        `;
        return;
    }

    elements.tableBody.innerHTML = pageData.map((alias, index) =>
        `<tr role="row"><td role="gridcell">${escapeHtml(alias.namespace)}</td><td role="gridcell">${escapeHtml(alias.resource_type)}</td><td role="gridcell">${escapeHtml(alias.alias_name)}</td><td role="gridcell">${escapeHtml(alias.default_path || 'N/A')}</td></tr>`
    ).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Pagination
function updatePagination() {
    const totalPages = Math.ceil(filteredAliases.length / itemsPerPage);

    if (totalPages <= 1) {
        hideElement(elements.pagination);
        return;
    }

    showElement(elements.pagination);
    elements.pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    elements.prevBtn.disabled = currentPage <= 1;
    elements.nextBtn.disabled = currentPage >= totalPages;
}

function goToPage(page) {
    const totalPages = Math.ceil(filteredAliases.length / itemsPerPage);
    if (page < 1 || page > totalPages) return;

    currentPage = page;
    renderTable();
    updatePagination();

    // Scroll to top of table smoothly
    elements.tableBody.parentElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Results info
function updateResultsInfo(queryTimeMs) {
    elements.resultsCount.textContent = filteredAliases.length.toLocaleString();
    if (queryTimeMs) {
        elements.queryTime.textContent = `(${queryTimeMs.toFixed(0)}ms)`;
    }
    showElement(elements.resultsInfo);
}

// Filter functionality
function applyFilters() {
    const query = elements.searchInput.value.trim().toLowerCase();
    const selectedNamespace = elements.namespaceFilter.value;

    if (!query && !selectedNamespace) {
        filteredAliases = allAliases;
    } else {
        const queryTerms = query.split(/\s+/).filter(t => t);

        filteredAliases = allAliases.filter(alias => {
            // Apply namespace filter
            if (selectedNamespace && alias.namespace !== selectedNamespace) {
                return false;
            }

            // Apply text search with multiple terms (AND logic)
            if (queryTerms.length > 0) {
                const searchText = [
                    alias.namespace,
                    alias.resource_type,
                    alias.alias_name,
                    alias.default_path || ''
                ].join(' ').toLowerCase();

                return queryTerms.every(term => searchText.includes(term));
            }

            return true;
        });
    }

    currentPage = 1;
    applySorting();
    updateResultsInfo();
    renderTable();
    updatePagination();
}

function clearFilters() {
    elements.searchInput.value = '';
    elements.namespaceFilter.value = '';
    sortColumn = null;
    sortDirection = 'asc';

    // Clear sort indicators
    document.querySelectorAll('th.sortable').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
    });

    applyFilters();
}

// Debounced search
let searchTimeout;
function debouncedSearch() {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(applyFilters, 300);
}

// Event listeners
document.addEventListener('DOMContentLoaded', async function () {
    // Initial load
    await loadAliases();
    await loadNamespaces();

    // Search input
    elements.searchInput.addEventListener('input', debouncedSearch);

    // Namespace filter
    elements.namespaceFilter.addEventListener('change', applyFilters);

    // Sorting
    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => setSortColumn(th.dataset.column));
        th.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setSortColumn(th.dataset.column);
            }
        });
    });

    // Export button
    elements.exportBtn.addEventListener('click', exportToCSV);

    // Refresh button
    elements.refreshBtn.addEventListener('click', async () => {
        const originalText = elements.refreshBtn.textContent;
        elements.refreshBtn.disabled = true;
        elements.refreshBtn.textContent = 'Refreshing...';

        try {
            await loadAliases(true);
            await loadNamespaces();
        } finally {
            elements.refreshBtn.disabled = false;
            elements.refreshBtn.textContent = originalText;
        }
    });

    // Clear filters button
    elements.clearFiltersBtn.addEventListener('click', clearFilters);

    // Pagination
    elements.prevBtn.addEventListener('click', () => goToPage(currentPage - 1));
    elements.nextBtn.addEventListener('click', () => goToPage(currentPage + 1));

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        // Ignore if user is typing in an input
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') {
            return;
        }

        if (e.ctrlKey || e.metaKey) {
            switch (e.key) {
                case 'k':
                    e.preventDefault();
                    elements.searchInput.focus();
                    elements.searchInput.select();
                    break;
                case 'r':
                    e.preventDefault();
                    elements.refreshBtn.click();
                    break;
                case 'e':
                    e.preventDefault();
                    exportToCSV();
                    break;
            }
        }
    });
});

// Handle errors globally
window.addEventListener('unhandledrejection', (event) => {
    console.error('Unhandled promise rejection:', event.reason);
    showError('An unexpected error occurred. Please refresh the page.');
});

// Performance monitoring
if (window.performance && window.performance.navigation.type === window.performance.navigation.TYPE_RELOAD) {
    console.info('Page reloaded');
}
