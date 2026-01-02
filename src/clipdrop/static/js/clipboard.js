/**
 * Clipboard Module
 * Handles clipboard create, copy, share, delete, and folder actions.
 */

(function() {
    'use strict';

    /**
     * Add loading state to a button
     * @param {HTMLElement} button - The button element
     */
    function setButtonLoading(button) {
        button.classList.add('is-loading');
        button.disabled = true;
    }

    /**
     * Remove loading state from a button
     * @param {HTMLElement} button - The button element
     */
    function clearButtonLoading(button) {
        button.classList.remove('is-loading');
        button.disabled = false;
    }

    /**
     * Show success feedback on a button
     * @param {HTMLElement} button - The button element
     * @param {string} originalHtml - Original button HTML to restore
     * @param {number} duration - Duration to show success state
     */
    function showButtonSuccess(button, originalHtml, duration = 2000) {
        button.innerHTML = '<i class="fas fa-check" aria-hidden="true"></i>';
        button.classList.add('btn-copy-success');

        setTimeout(() => {
            button.innerHTML = originalHtml;
            button.classList.remove('btn-copy-success');
        }, duration);
    }

    async function copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (err) {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            try {
                document.execCommand('copy');
                document.body.removeChild(textarea);
                return true;
            } catch (e) {
                document.body.removeChild(textarea);
                return false;
            }
        }
    }

    async function postJson(url, payload) {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        return response.json();
    }

    function handleClipboardForm() {
        const clipboardForm = document.getElementById('clipboard-form');
        if (!clipboardForm) return;

        clipboardForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const textArea = clipboardForm.querySelector('[name="clipboard_data"]');
            const text = textArea ? textArea.value.trim() : '';
            if (!text) {
                if (window.showToast) {
                    window.showToast('Please enter some text', 'error');
                }
                return;
            }

            const formData = new FormData(clipboardForm);

            try {
                const response = await fetch('/clipboard', { method: 'POST', body: formData });
                const result = await response.json();

                if (result.status === 'success') {
                    if (window.showToast) {
                        window.showToast('Saved to clipboard!', 'success');
                    }
                    setTimeout(() => location.reload(), 1000);
                } else if (window.showToast) {
                    window.showToast(result.message || 'Failed to save', 'error');
                }
            } catch (error) {
                if (window.showToast) {
                    window.showToast('Failed to save', 'error');
                }
            }
        });
    }

    function handleCopyButtons() {
        document.querySelectorAll('.copy-content-btn').forEach((button) => {
            button.addEventListener('click', async () => {
                const content = button.getAttribute('data-content');
                const success = await copyToClipboard(content);
                if (window.showToast) {
                    window.showToast(success ? 'Copied to clipboard!' : 'Failed to copy', success ? 'success' : 'error');
                }

                if (success) {
                    const originalHtml = button.innerHTML;
                    button.innerHTML = '<i class="fas fa-check" aria-hidden="true"></i>';
                    button.classList.add('btn-success');
                    button.classList.remove('btn-primary');

                    setTimeout(() => {
                        button.innerHTML = originalHtml;
                        button.classList.remove('btn-success');
                        button.classList.add('btn-primary');
                    }, 2000);
                }
            });
        });
    }

    function handleShareButtons() {
        document.querySelectorAll('.share-btn').forEach((button) => {
            button.addEventListener('click', async () => {
                const url = button.getAttribute('data-url');
                const success = await copyToClipboard(url);
                if (window.showToast) {
                    window.showToast(success ? 'Link copied!' : 'Failed to copy link', success ? 'success' : 'error');
                }
            });
        });
    }

    function handleDeleteButtons() {
        document.querySelectorAll('.clipboard-delete-btn').forEach((button) => {
            button.addEventListener('click', () => {
                const itemId = button.getAttribute('data-id');
                const name = button.getAttribute('data-name') || 'this item';
                const itemName = name.length > 30 ? name.substring(0, 30) + '...' : name;

                const doDelete = async () => {
                    try {
                        const result = await postJson(`/clipboard/${itemId}/delete`, {});
                        if (result.status === 'success') {
                            if (window.showToast) {
                                window.showToast('Deleted!', 'success');
                            }
                            setTimeout(() => location.reload(), 1000);
                        } else if (window.showToast) {
                            window.showToast(result.message || 'Failed to delete', 'error');
                        }
                    } catch (error) {
                        if (window.showToast) {
                            window.showToast('Failed to delete', 'error');
                        }
                    }
                };

                if (window.showConfirmModal) {
                    window.showConfirmModal({
                        title: 'Delete Clipboard Item',
                        message: `Are you sure you want to delete "${itemName}"? This action cannot be undone.`,
                        confirmText: 'Delete',
                        onConfirm: doDelete
                    });
                } else if (confirm(`Delete "${itemName}"?`)) {
                    doDelete();
                }
            });
        });
    }

    function handleFavoriteToggles() {
        document.querySelectorAll('.favorite-toggle').forEach((button) => {
            button.addEventListener('click', async () => {
                const itemId = button.getAttribute('data-id');
                const current = button.getAttribute('data-favorite') === 'true';
                const target = !current;
                try {
                    const result = await postJson(`/clipboard/${itemId}/favorite`, { favorite: target });
                    if (result.status === 'success') {
                        if (window.showToast) {
                            window.showToast(result.favorite ? 'Added to favorites' : 'Removed from favorites', 'success');
                        }
                        setTimeout(() => location.reload(), 500);
                    } else if (window.showToast) {
                        window.showToast(result.message || 'Failed to update favorite', 'error');
                    }
                } catch (error) {
                    if (window.showToast) {
                        window.showToast('Failed to update favorite', 'error');
                    }
                }
            });
        });
    }

    function handleRetentionToggles() {
        document.querySelectorAll('.retention-toggle').forEach((button) => {
            button.addEventListener('click', async () => {
                const itemId = button.getAttribute('data-id');
                const current = button.getAttribute('data-keep') === 'true';
                const target = !current;
                try {
                    const result = await postJson(`/clipboard/${itemId}/retention`, { keep: target });
                    if (result.status === 'success') {
                        if (window.showToast) {
                            window.showToast(
                                result.kept ? 'Kept indefinitely' : 'Retention restored',
                                'success'
                            );
                        }
                        setTimeout(() => location.reload(), 500);
                    } else if (window.showToast) {
                        window.showToast(result.message || 'Failed to update retention', 'error');
                    }
                } catch (error) {
                    if (window.showToast) {
                        window.showToast('Failed to update retention', 'error');
                    }
                }
            });
        });
    }

    function handleFolderCreate() {
        const folderForm = document.getElementById('folder-create-form');
        if (!folderForm) return;

        folderForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const formData = new FormData(folderForm);
            const name = formData.get('name');
            if (!name || !name.trim()) {
                if (window.showToast) {
                    window.showToast('Folder name is required', 'error');
                }
                return;
            }

            try {
                const response = await fetch('/clipboard/folders', { method: 'POST', body: formData });
                const result = await response.json();
                if (result.status === 'success') {
                    if (window.showToast) {
                        window.showToast('Folder created', 'success');
                    }
                    setTimeout(() => location.reload(), 500);
                } else if (window.showToast) {
                    window.showToast(result.message || 'Failed to create folder', 'error');
                }
            } catch (error) {
                if (window.showToast) {
                    window.showToast('Failed to create folder', 'error');
                }
            }
        });
    }

    function handleFolderRename() {
        document.querySelectorAll('.folder-rename-btn').forEach((button) => {
            button.addEventListener('click', async () => {
                const folderId = button.getAttribute('data-id');
                const currentName = button.getAttribute('data-name') || '';
                const newName = prompt('Rename folder', currentName);
                if (!newName || !newName.trim()) {
                    return;
                }
                try {
                    const result = await postJson(`/clipboard/folders/${folderId}/rename`, { name: newName.trim() });
                    if (result.status === 'success') {
                        if (window.showToast) {
                            window.showToast('Folder renamed', 'success');
                        }
                        setTimeout(() => location.reload(), 500);
                    } else if (window.showToast) {
                        window.showToast(result.message || 'Failed to rename folder', 'error');
                    }
                } catch (error) {
                    if (window.showToast) {
                        window.showToast('Failed to rename folder', 'error');
                    }
                }
            });
        });
    }

    function handleFolderDelete() {
        document.querySelectorAll('.folder-delete-btn').forEach((button) => {
            button.addEventListener('click', () => {
                const folderId = button.getAttribute('data-id');
                const doDelete = async () => {
                    try {
                        const result = await postJson(`/clipboard/folders/${folderId}/delete`, {});
                        if (result.status === 'success') {
                            if (window.showToast) {
                                window.showToast('Folder deleted', 'success');
                            }
                            setTimeout(() => location.reload(), 500);
                        } else if (window.showToast) {
                            window.showToast(result.message || 'Failed to delete folder', 'error');
                        }
                    } catch (error) {
                        if (window.showToast) {
                            window.showToast('Failed to delete folder', 'error');
                        }
                    }
                };

                if (window.showConfirmModal) {
                    window.showConfirmModal({
                        title: 'Delete Folder',
                        message: 'Delete this folder? It must be empty before removal.',
                        confirmText: 'Delete',
                        onConfirm: doDelete
                    });
                } else if (confirm('Delete this folder? It must be empty before removal.')) {
                    doDelete();
                }
            });
        });
    }

    function initClipboardHandlers() {
        handleClipboardForm();
        handleCopyButtons();
        handleShareButtons();
        handleDeleteButtons();
        handleFavoriteToggles();
        handleRetentionToggles();
        handleFolderCreate();
        handleFolderRename();
        handleFolderDelete();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initClipboardHandlers);
    } else {
        initClipboardHandlers();
    }

    window.ClipDrop = window.ClipDrop || {};
    window.ClipDrop.clipboard = {
        copy: copyToClipboard,
        init: initClipboardHandlers
    };
})();
