// Solana Scanner — Frontend JS

async function scanWallet(address) {
    const modal = document.getElementById('scan-modal');
    const status = document.getElementById('scan-status');
    const progress = document.getElementById('scan-progress');

    if (!modal || !status || !progress) return;

    modal.classList.remove('hidden');
    status.textContent = 'Fetching and processing transactions...';
    progress.style.width = '10%';

    try {
        // Simulate progress while waiting
        let pct = 10;
        const interval = setInterval(() => {
            if (pct < 90) {
                pct += Math.random() * 5;
                progress.style.width = pct + '%';
            }
        }, 500);

        const response = await fetch(`/wallet/${address}/scan`, { method: 'POST' });
        clearInterval(interval);

        if (response.ok) {
            const data = await response.json();
            progress.style.width = '100%';
            status.textContent =
                `Done! ${data.new_transactions} new transactions fetched, ` +
                `${data.normalized} normalized, ${data.classified} classified.`;

            // Reload page after a brief delay
            setTimeout(() => {
                window.location.reload();
            }, 1500);
        } else {
            const err = await response.json();
            status.textContent = `Error: ${err.message || 'Scan failed'}`;
            progress.style.width = '0%';
        }
    } catch (e) {
        status.textContent = `Network error: ${e.message}`;
        progress.style.width = '0%';
    }
}

function closeScanModal() {
    const modal = document.getElementById('scan-modal');
    if (modal) modal.classList.add('hidden');
}
