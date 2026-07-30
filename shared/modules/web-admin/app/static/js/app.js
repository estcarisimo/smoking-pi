/*
 * Smoking Pi web-admin shared helpers.
 *
 * Provides:
 *   csrfToken()      - read the CSRF token from the <meta> tag
 *   showToast()      - Bootstrap toast notifications (replaces alert())
 *   confirmDialog()  - Promise-based Bootstrap confirm modal (replaces confirm())
 *   fetchJSON()      - fetch() wrapper adding the X-CSRFToken header and
 *                      uniform error handling
 *
 * Vanilla JS + Bootstrap only. No build step.
 */

'use strict';

function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

function showToast(title, message, type = 'info') {
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '1080';
        document.body.appendChild(toastContainer);
    }

    const textClass = type === 'warning' || type === 'light' ? '' : 'text-white';
    const toastEl = document.createElement('div');
    toastEl.className = `toast align-items-center ${textClass} bg-${type} border-0`;
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');

    const flex = document.createElement('div');
    flex.className = 'd-flex';

    const body = document.createElement('div');
    body.className = 'toast-body';
    const strong = document.createElement('strong');
    strong.textContent = `${title}: `;
    body.appendChild(strong);
    body.appendChild(document.createTextNode(message));

    const close = document.createElement('button');
    close.type = 'button';
    close.className = `btn-close ${textClass ? 'btn-close-white' : ''} me-2 m-auto`;
    close.setAttribute('data-bs-dismiss', 'toast');
    close.setAttribute('aria-label', 'Close');

    flex.appendChild(body);
    flex.appendChild(close);
    toastEl.appendChild(flex);
    toastContainer.appendChild(toastEl);

    const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

/*
 * Reusable confirm modal. Returns a Promise resolving to true (confirmed)
 * or false (dismissed).
 *
 *   confirmDialog({ title, message, confirmText, confirmClass })
 */
function confirmDialog(options = {}) {
    const {
        title = 'Please Confirm',
        message = 'Are you sure?',
        confirmText = 'Confirm',
        confirmClass = 'btn-primary',
    } = options;

    let modalEl = document.getElementById('app-confirm-modal');
    if (!modalEl) {
        modalEl = document.createElement('div');
        modalEl.id = 'app-confirm-modal';
        modalEl.className = 'modal fade';
        modalEl.tabIndex = -1;
        modalEl.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title"></h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body"></div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                        <button type="button" class="btn" id="app-confirm-modal-ok"></button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(modalEl);
    }

    modalEl.querySelector('.modal-title').textContent = title;
    modalEl.querySelector('.modal-body').textContent = message;
    const okBtn = modalEl.querySelector('#app-confirm-modal-ok');
    okBtn.className = `btn ${confirmClass}`;
    okBtn.textContent = confirmText;

    return new Promise((resolve) => {
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        let confirmed = false;

        const onConfirm = () => {
            confirmed = true;
            modal.hide();
        };
        const onHidden = () => {
            okBtn.removeEventListener('click', onConfirm);
            modalEl.removeEventListener('hidden.bs.modal', onHidden);
            resolve(confirmed);
        };

        okBtn.addEventListener('click', onConfirm);
        modalEl.addEventListener('hidden.bs.modal', onHidden);
        modal.show();
    });
}

/*
 * fetch() wrapper for JSON APIs.
 *
 * - Adds X-CSRFToken on state-changing requests.
 * - Serializes a plain-object body to JSON.
 * - Rejects with an Error carrying the server-provided message on non-2xx
 *   responses ("error" or "message" keys), plus err.status and err.data.
 */
async function fetchJSON(url, options = {}) {
    const opts = { ...options };
    opts.headers = { Accept: 'application/json', ...(options.headers || {}) };

    const method = (opts.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
        opts.headers['X-CSRFToken'] = csrfToken();
    }

    if (opts.body && typeof opts.body === 'object'
            && !(opts.body instanceof FormData)
            && !(opts.body instanceof Blob)
            && typeof opts.body !== 'string') {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.body);
    }

    const response = await fetch(url, opts);
    let data = null;
    try {
        data = await response.json();
    } catch (e) {
        data = null;
    }

    if (!response.ok) {
        const message = (data && (data.error || data.message))
            || `Request failed (HTTP ${response.status})`;
        const err = new Error(message);
        err.status = response.status;
        err.data = data;
        throw err;
    }
    return data || {};
}
