// ─────────────────────────────────────────────────────────────────────────────
// MODAL, TOAST, INSTRUCTION, STATUS, SPINNER
// ─────────────────────────────────────────────────────────────────────────────
const instrEl   = document.getElementById('instruction');
const instrStep = document.getElementById('instr-step');
const instrText = document.getElementById('instr-text');

export function showInstruction(step, text, variant='blue') {
  instrStep.textContent = step;
  instrStep.className = 'instr-step' +
    (variant==='green' ? ' green' : variant==='amber' ? ' amber' : '');
  instrText.textContent = text;
  instrEl.classList.add('visible');
}
export function hideInstruction() { instrEl.classList.remove('visible'); }

// ─────────────────────────────────────────────────────────────────────────────
// MODAL — promise-based confirmation dialog
// showModal(options) → Promise<{values}> resolves on Confirm, rejects on Cancel
// ─────────────────────────────────────────────────────────────────────────────
const modalOverlay = document.getElementById('modal-overlay');
const modalTitle   = document.getElementById('modal-title');
const modalDesc    = document.getElementById('modal-desc');
const modalFields  = document.getElementById('modal-fields');
const modalConfirm = document.getElementById('modal-confirm');
const modalCancel  = document.getElementById('modal-cancel');

let _modalResolve = null, _modalReject = null;

function escapeHTML(v) {
  return String(v ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

export function showModal({ title, desc, fields = [], confirmLabel = 'Confirm', confirmClass = 'primary', danger = false }) {
  return new Promise((resolve, reject) => {
    _modalResolve = resolve;
    _modalReject  = reject;

    modalTitle.textContent = title;
    modalDesc.textContent  = desc || '';
    modalConfirm.textContent = confirmLabel;
    modalConfirm.className = `btn ${danger ? 'danger' : confirmClass}`;

    // Render input fields
    modalFields.innerHTML = '';
    for (const f of fields) {
      const div = document.createElement('div');
      div.className = 'modal-field';
      const value = f.value ?? '';
      if (f.type === 'select') {
        const options = (f.options || []).map(opt =>
          `<option value="${escapeHTML(opt.value)}"${String(opt.value) === String(value) ? ' selected' : ''}>${escapeHTML(opt.label)}</option>`
        ).join('');
        div.innerHTML = `<label>${escapeHTML(f.label)}</label>
          <select id="modal-field-${escapeHTML(f.key)}">${options}</select>`;
      } else {
        div.innerHTML = `<label>${escapeHTML(f.label)}</label>
          <input id="modal-field-${escapeHTML(f.key)}" type="text"
                 value="${escapeHTML(value)}"
                 placeholder="${escapeHTML(f.placeholder || '')}" />`;
      }
      modalFields.appendChild(div);
    }

    modalOverlay.classList.add('visible');

    // Focus first input if present
    const first = modalFields.querySelector('input, select');
    if (first) setTimeout(() => first.focus(), 60);
  });
}

export function _closeModal(ok) {
  modalOverlay.classList.remove('visible');
  if (!ok) { _modalReject && _modalReject(new Error('cancelled')); return; }
  const values = {};
  modalFields.querySelectorAll('input, select').forEach(inp => {
    const key = inp.id.replace('modal-field-', '');
    values[key] = inp.value.trim();
  });
  _modalResolve && _modalResolve(values);
}

// Wire up modal event listeners at module load time
modalConfirm.addEventListener('click', () => _closeModal(true));
modalCancel.addEventListener('click',  () => _closeModal(false));
modalOverlay.addEventListener('click', e => { if (e.target === modalOverlay) _closeModal(false); });

// Enter confirms, Escape cancels (when modal is open)
document.addEventListener('keydown', e => {
  if (!modalOverlay.classList.contains('visible')) return;
  if (e.key === 'Enter')  { e.preventDefault(); _closeModal(true);  }
  if (e.key === 'Escape') { e.preventDefault(); _closeModal(false); }
});

// ─────────────────────────────────────────────────────────────────────────────
// TOAST
// ─────────────────────────────────────────────────────────────────────────────
const toast = document.getElementById('toast');

export function showToast(msg, type='info') {
  toast.textContent = msg;
  toast.className = 'show ' + type;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toast.className = ''; }, 3000);
}

// ─────────────────────────────────────────────────────────────────────────────
// STATUS BAR
// ─────────────────────────────────────────────────────────────────────────────
const statusDot  = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');

export function setStatus(text, st='ok') {
  statusText.textContent = text;
  statusDot.className = 'status-dot ' + st;
}

// ─────────────────────────────────────────────────────────────────────────────
// SPINNER
// ─────────────────────────────────────────────────────────────────────────────
const spinner = document.getElementById('spinner');

export function showSpinner(v) {
  spinner.classList.toggle('visible', v);
}
