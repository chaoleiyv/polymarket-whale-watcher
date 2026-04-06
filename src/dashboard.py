"""FastAPI dashboard for signal performance tracking."""
import logging
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from src.config import get_settings
from src.db.database import SignalDatabase
from src.services.stats_engine import StatsEngine

logger = logging.getLogger(__name__)

app = FastAPI(title="Polymarket Whale Watcher - Signal Dashboard")


def _get_db() -> SignalDatabase:
    settings = get_settings()
    return SignalDatabase(settings.db_path)


@app.get("/api/stats")
def api_stats():
    """Overall signal performance statistics."""
    db = _get_db()
    return db.get_stats()


@app.get("/api/stats/tiers")
def api_stats_tiers():
    """Signal stats by information_asymmetry_score tier."""
    db = _get_db()
    return db.get_stats_by_tier()


@app.get("/api/signals")
def api_signals(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Paginated signal list (newest first)."""
    db = _get_db()
    signals = db.get_all_signals(limit=limit, offset=offset)
    return [s.model_dump(mode="json") for s in signals]


@app.get("/api/signals/best-worst")
def api_best_worst(n: int = Query(5, ge=1, le=20)):
    """Best and worst signals by theoretical ROI."""
    db = _get_db()
    result = db.get_best_worst(n=n)
    return {
        "best": [s.model_dump(mode="json") for s in result["best"]],
        "worst": [s.model_dump(mode="json") for s in result["worst"]],
    }


@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    """HTML dashboard page."""
    return HTML_TEMPLATE


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket Whale Watcher - Signal Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e0e0e0; padding: 20px; }
h1 { color: #fff; margin-bottom: 8px; font-size: 1.8em; }
h2 { color: #a0a8c0; margin: 24px 0 12px; font-size: 1.2em; }
.subtitle { color: #666; margin-bottom: 24px; }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
.stat-card { background: #1a1d28; border-radius: 8px; padding: 16px; text-align: center; }
.stat-value { font-size: 1.8em; font-weight: bold; color: #4fc3f7; }
.stat-value.green { color: #66bb6a; }
.stat-value.red { color: #ef5350; }
.stat-label { color: #888; font-size: 0.85em; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
th { background: #1a1d28; color: #a0a8c0; text-align: left; padding: 10px 12px; font-weight: 600; font-size: 0.85em; }
td { padding: 10px 12px; border-bottom: 1px solid #222; font-size: 0.9em; }
tr:hover { background: #1a1d28; }
.correct { color: #66bb6a; }
.incorrect { color: #ef5350; }
.pending { color: #888; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }
.badge-high { background: #ef535033; color: #ef5350; }
.badge-med { background: #ffb74d33; color: #ffb74d; }
.badge-low { background: #66bb6a33; color: #66bb6a; }
.tier-table th, .tier-table td { text-align: center; }
#loading { color: #666; text-align: center; padding: 40px; }
</style>
</head>
<body>

<h1>Polymarket Whale Watcher</h1>
<p class="subtitle">Signal Performance Dashboard</p>

<div id="loading">Loading...</div>
<div id="content" style="display:none">

<div class="stats-grid" id="stats-grid"></div>

<h2>Stats by Likelihood Tier</h2>
<table class="tier-table" id="tier-table">
<thead><tr><th>Tier</th><th>Total</th><th>Resolved</th><th>Correct</th><th>Win Rate</th><th>Avg ROI</th></tr></thead>
<tbody></tbody>
</table>

<h2>Best Signals</h2>
<table id="best-table">
<thead><tr><th>Market</th><th>Side</th><th>Price</th><th>Size</th><th>Likelihood</th><th>Outcome</th><th>ROI</th></tr></thead>
<tbody></tbody>
</table>

<h2>Worst Signals</h2>
<table id="worst-table">
<thead><tr><th>Market</th><th>Side</th><th>Price</th><th>Size</th><th>Likelihood</th><th>Outcome</th><th>ROI</th></tr></thead>
<tbody></tbody>
</table>

<h2>Recent Signals</h2>
<table id="signals-table">
<thead><tr><th>Detected</th><th>Market</th><th>Side</th><th>Price</th><th>Size</th><th>Likelihood</th><th>Result</th><th>ROI</th></tr></thead>
<tbody></tbody>
</table>

</div>

<script>
const fmt = (v, d=1) => v !== null && v !== undefined ? (v*100).toFixed(d)+'%' : 'N/A';
const fmtRoi = v => v !== null && v !== undefined ? (v >= 0 ? '+' : '') + (v*100).toFixed(1)+'%' : 'Pending';
const fmtUsd = v => '$' + Number(v).toLocaleString('en-US', {maximumFractionDigits: 0});
const likeBadge = v => {
    if (v >= 0.8) return `<span class="badge badge-high">${fmt(v,0)}</span>`;
    if (v >= 0.6) return `<span class="badge badge-med">${fmt(v,0)}</span>`;
    return `<span class="badge badge-low">${fmt(v,0)}</span>`;
};
const resultClass = s => {
    if (s.signal_correct === true) return 'correct';
    if (s.signal_correct === false) return 'incorrect';
    return 'pending';
};
const resultText = s => {
    if (!s.market_resolved) return 'Pending';
    return s.signal_correct ? 'Correct' : 'Incorrect';
};

function signalRow(s, showDate=true) {
    const cols = [];
    if (showDate) cols.push(`<td>${(s.detected_at||'').slice(0,16)}</td>`);
    cols.push(`<td>${(s.market_question||'').slice(0,60)}</td>`);
    cols.push(`<td>${s.trade_side} ${s.trade_outcome}</td>`);
    cols.push(`<td>${Number(s.trade_price).toFixed(4)}</td>`);
    cols.push(`<td>${fmtUsd(s.trade_size_usd)}</td>`);
    cols.push(`<td>${likeBadge(s.information_asymmetry_score)}</td>`);
    if (showDate) cols.push(`<td class="${resultClass(s)}">${resultText(s)}</td>`);
    else cols.push(`<td>${s.resolved_outcome||'Pending'}</td>`);
    cols.push(`<td class="${resultClass(s)}">${fmtRoi(s.theoretical_roi)}</td>`);
    return '<tr>' + cols.join('') + '</tr>';
}

async function load() {
    try {
        const [statsRes, tiersRes, bwRes, sigRes] = await Promise.all([
            fetch('/api/stats'), fetch('/api/stats/tiers'),
            fetch('/api/signals/best-worst?n=5'), fetch('/api/signals?limit=100')
        ]);
        const stats = await statsRes.json();
        const tiers = await tiersRes.json();
        const bw = await bwRes.json();
        const signals = await sigRes.json();

        // Stats cards
        const grid = document.getElementById('stats-grid');
        const cards = [
            ['Total Signals', stats.total_signals, ''],
            ['Resolved', stats.resolved, ''],
            ['Win Rate', fmt(stats.win_rate), stats.win_rate >= 0.5 ? 'green' : 'red'],
            ['Avg ROI', fmtRoi(stats.avg_roi), stats.avg_roi >= 0 ? 'green' : 'red'],
            ['Correct', stats.correct, 'green'],
            ['Total PnL', (stats.total_theoretical_pnl >= 0 ? '+' : '') + Number(stats.total_theoretical_pnl).toFixed(2) + 'x', stats.total_theoretical_pnl >= 0 ? 'green' : 'red'],
        ];
        grid.innerHTML = cards.map(([label, value, cls]) =>
            `<div class="stat-card"><div class="stat-value ${cls}">${value}</div><div class="stat-label">${label}</div></div>`
        ).join('');

        // Tier table
        const tierBody = document.querySelector('#tier-table tbody');
        tierBody.innerHTML = tiers.map(t =>
            `<tr><td>${t.tier}</td><td>${t.total}</td><td>${t.resolved}</td><td>${t.correct}</td><td>${t.resolved > 0 ? fmt(t.win_rate) : 'N/A'}</td><td>${t.resolved > 0 ? fmtRoi(t.avg_roi) : 'N/A'}</td></tr>`
        ).join('');

        // Best/worst
        document.querySelector('#best-table tbody').innerHTML = bw.best.map(s => signalRow(s, false)).join('');
        document.querySelector('#worst-table tbody').innerHTML = bw.worst.map(s => signalRow(s, false)).join('');

        // All signals
        document.querySelector('#signals-table tbody').innerHTML = signals.map(s => signalRow(s)).join('');

        document.getElementById('loading').style.display = 'none';
        document.getElementById('content').style.display = 'block';
    } catch(e) {
        document.getElementById('loading').textContent = 'Error loading data: ' + e.message;
    }
}
load();
</script>
</body>
</html>"""
