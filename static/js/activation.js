async function postJson(url, payload) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data.message || data.error || `HTTP ${res.status}`);
    }
    return data;
}

function setResult(message, ok) {
    const box = document.getElementById('activationResult');
    if (!box) return;
    box.className = ok ? 'ok' : 'err';
    box.textContent = message || '';
}

function getSelectedPlanKey() {
    const checked = document.querySelector('input[name="activationPlan"]:checked');
    return checked ? checked.value : '1m';
}

function getSelectedPlanLabel() {
    const checked = document.querySelector('input[name="activationPlan"]:checked');
    const label = checked?.closest('.plan-item')?.querySelector('.plan-label');
    return label ? label.textContent.trim() : '1 Tháng';
}

function refreshSelectedPlanUI() {
    document.querySelectorAll('.plan-item').forEach(item => {
        const radio = item.querySelector('input[type="radio"]');
        item.classList.toggle('active', !!radio?.checked);
    });
    const target = document.getElementById('selectedPlanLabel');
    if (target) target.textContent = getSelectedPlanLabel();
}

async function checkStatus() {
    try {
        const res = await fetch('/api/activation/status');
        const data = await res.json();
        setResult(data.message || 'Đã kiểm tra trạng thái.', !!data.activated);
        if (data.activated) {
            setTimeout(() => window.location.href = '/guide', 900);
        }
    } catch (err) {
        setResult(err.message, false);
    }
}

window.checkStatus = checkStatus;

document.addEventListener('DOMContentLoaded', () => {
    const saveBtn = document.getElementById('saveActivationBtn');
    const resendBtn = document.getElementById('resendActivationBtn');
    const requestBtn = document.getElementById('requestActivationBtn');
    const codeEl = document.getElementById('activationCode');

    document.querySelectorAll('.plan-item').forEach(item => {
        item.addEventListener('click', () => {
            const radio = item.querySelector('input[type="radio"]');
            if (radio) radio.checked = true;
            refreshSelectedPlanUI();
        });
    });
    refreshSelectedPlanUI();

    if (codeEl) {
        codeEl.addEventListener('input', () => {
            codeEl.value = (codeEl.value || '').replace(/[^a-zA-Z0-9]/g, '').toUpperCase().slice(0, 12);
        });
    }

    async function requestCode(buttonRef, loadingText) {
        const planKey = getSelectedPlanKey();
        const planLabel = getSelectedPlanLabel();
        buttonRef.disabled = true;
        const oldText = buttonRef.textContent;
        buttonRef.textContent = loadingText;
        try {
            const data = await postJson('/api/activation/resend', { duration_key: planKey });
            setResult(data.message || `Đã gửi mã cho gói ${planLabel}.`, true);
        } catch (err) {
            setResult(err.message, false);
        } finally {
            buttonRef.disabled = false;
            buttonRef.textContent = oldText;
        }
    }

    if (requestBtn) {
        requestBtn.addEventListener('click', async () => {
            await requestCode(requestBtn, 'Đang gửi...');
        });
    }

    if (resendBtn) {
        resendBtn.addEventListener('click', async () => {
            await requestCode(resendBtn, 'Đang gửi lại...');
        });
    }

    if (saveBtn) {
        saveBtn.addEventListener('click', async () => {
            const code = (codeEl?.value || '').replace(/[^a-zA-Z0-9]/g, '').toUpperCase().trim();
            if (!code) {
                setResult('Vui lòng nhập mã kích hoạt trước.', false);
                return;
            }
            if (code.length !== 12) {
                setResult('Mã kích hoạt phải đúng 12 ký tự.', false);
                return;
            }
            saveBtn.disabled = true;
            saveBtn.textContent = 'Đang lưu...';
            try {
                const data = await postJson('/api/activation/save', { code });
                setResult(data.status_message || data.message || 'Kích hoạt thành công.', true);
                setTimeout(() => window.location.href = '/guide', 900);
            } catch (err) {
                setResult(err.message, false);
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Lưu & kích hoạt';
            }
        });
    }
});
