"""End-to-end test for signal tracking & verification system."""
import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from src.db.database import SignalDatabase
from src.models.anomaly_signal import AnomalySignal
from src.models.trade import TraderRanking, TraderHistory
from src.services.anomaly_history import AnomalyHistoryService
from src.services.stats_engine import StatsEngine
from src.services.resolution_tracker import ResolutionTracker


# ── Helpers ──────────────────────────────────────────────────────────────

def make_signal(
    market_id: str,
    tx_hash: str,
    outcome: str = "Yes",
    price: float = 0.40,
    size: float = 10000.0,
    likelihood: float = 0.65,
    question: str = "Test market?",
    condition_id: str = "cond_1",
    detected_hours_ago: int = 0,
) -> AnomalySignal:
    return AnomalySignal(
        id=f"{market_id}_{tx_hash}",
        market_id=market_id,
        market_question=question,
        market_slug=f"test-{market_id}",
        condition_id=condition_id,
        transaction_hash=tx_hash,
        trade_timestamp=int((datetime.utcnow() - timedelta(hours=detected_hours_ago)).timestamp()),
        trade_side="BUY",
        trade_price=price,
        trade_size_usd=size,
        trade_outcome=outcome,
        trader_wallet="0xabc123",
        trader_ranking=TraderRanking(rank=50, pnl=120000.0, volume=500000.0, user_name="TestWhale"),
        trader_history=TraderHistory(total_trades=80, total_volume=300000.0, avg_trade_size=3750.0, large_trades_count=10),
        information_asymmetry_score=likelihood,
        reasoning="测试信号 — 大额交易者在低价位买入",
        insider_evidence="1. 排名前100交易者 2. 交易金额远超平均",
        detected_at=datetime.utcnow() - timedelta(hours=detected_hours_ago),
    )


passed = 0
failed = 0

def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        msg = f"  ❌ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


# ── Test 1: Database CRUD ────────────────────────────────────────────────

def test_database_crud():
    print("\n🧪 Test 1: Database CRUD")

    db_path = tempfile.mktemp(suffix=".db")
    db = SignalDatabase(db_path)

    # Insert
    s1 = make_signal("m1", "tx_001", outcome="Yes", price=0.35, likelihood=0.70)
    s2 = make_signal("m1", "tx_002", outcome="Yes", price=0.40, likelihood=0.55)
    s3 = make_signal("m2", "tx_003", outcome="No", price=0.60, likelihood=0.85)

    check("Insert signal 1", db.insert_signal(s1))
    check("Insert signal 2", db.insert_signal(s2))
    check("Insert signal 3", db.insert_signal(s3))

    # Dedup
    check("Duplicate rejected", not db.insert_signal(s1))

    # Query by market
    m1_signals = db.get_signals_for_market("m1")
    check("get_signals_for_market count", len(m1_signals) == 2, f"got {len(m1_signals)}")

    # Verify fields roundtrip
    sig = m1_signals[0]
    check("Signal fields roundtrip — market_id", sig.market_id == "m1")
    check("Signal fields roundtrip — condition_id", sig.condition_id == "cond_1")
    check("Signal fields roundtrip — trader_ranking", sig.trader_ranking is not None and sig.trader_ranking.rank == 50)
    check("Signal fields roundtrip — trader_history", sig.trader_history is not None and sig.trader_history.total_trades == 80)
    check("Signal fields roundtrip — IAS", sig.information_asymmetry_score == 0.70 or sig.information_asymmetry_score == 0.55)

    # get_all_market_ids
    market_ids = db.get_all_market_ids()
    check("get_all_market_ids", set(market_ids) == {"m1", "m2"}, f"got {market_ids}")

    # get_signal_count
    check("get_signal_count total", db.get_signal_count() == 3)
    check("get_signal_count m1", db.get_signal_count("m1") == 2)

    # Pagination
    page = db.get_all_signals(limit=2, offset=0)
    check("Pagination limit=2", len(page) == 2)
    page2 = db.get_all_signals(limit=2, offset=2)
    check("Pagination offset=2", len(page2) == 1)

    # Unresolved
    unresolved = db.get_unresolved_market_ids()
    check("Unresolved markets", set(unresolved) == {"m1", "m2"})

    # Stats before resolution
    stats = db.get_stats()
    check("Stats total", stats["total_signals"] == 3)
    check("Stats resolved=0", stats["resolved"] == 0)

    Path(db_path).unlink(missing_ok=True)
    print(f"  [cleanup: {db_path}]")


# ── Test 2: Resolution & ROI ─────────────────────────────────────────────

def test_resolution():
    print("\n🧪 Test 2: Market Resolution & ROI Calculation")

    db_path = tempfile.mktemp(suffix=".db")
    db = SignalDatabase(db_path)

    # Signal: BUY Yes @ 0.35 → if resolved Yes, ROI = (1-0.35)/0.35 = 1.857
    s1 = make_signal("m1", "tx_r1", outcome="Yes", price=0.35, likelihood=0.70)
    # Signal: BUY No @ 0.60 → if resolved Yes, ROI = -1.0 (wrong direction)
    s2 = make_signal("m1", "tx_r2", outcome="No", price=0.60, likelihood=0.50)
    # Signal: BUY No @ 0.30 → if resolved No, ROI = (1-0.30)/0.30 = 2.333
    s3 = make_signal("m2", "tx_r3", outcome="No", price=0.30, likelihood=0.80)

    db.insert_signal(s1)
    db.insert_signal(s2)
    db.insert_signal(s3)

    # Resolve m1 as "Yes"
    updated = db.mark_market_resolved("m1", "Yes", datetime.utcnow())
    check("mark_market_resolved count", updated == 2, f"got {updated}")

    # Verify s1 correct
    m1_signals = db.get_signals_for_market("m1")
    s1_result = next(s for s in m1_signals if s.transaction_hash == "tx_r1")
    check("s1 market_resolved=True", s1_result.market_resolved)
    check("s1 resolved_outcome=Yes", s1_result.resolved_outcome == "Yes")
    check("s1 signal_correct=True", s1_result.signal_correct == True)
    expected_roi = (1 - 0.35) / 0.35
    check("s1 ROI ~1.857", abs(s1_result.theoretical_roi - expected_roi) < 0.01, f"got {s1_result.theoretical_roi}")

    # Verify s2 incorrect
    s2_result = next(s for s in m1_signals if s.transaction_hash == "tx_r2")
    check("s2 signal_correct=False", s2_result.signal_correct == False)
    check("s2 ROI = -1.0", s2_result.theoretical_roi == -1.0)

    # Resolve m2 as "No"
    db.mark_market_resolved("m2", "No", datetime.utcnow())
    m2_signals = db.get_signals_for_market("m2")
    s3_result = m2_signals[0]
    check("s3 signal_correct=True", s3_result.signal_correct == True)
    expected_roi_3 = (1 - 0.30) / 0.30
    check("s3 ROI ~2.333", abs(s3_result.theoretical_roi - expected_roi_3) < 0.01, f"got {s3_result.theoretical_roi}")

    # Stats after resolution
    stats = db.get_stats()
    check("Stats resolved=3", stats["resolved"] == 3)
    check("Stats correct=2", stats["correct"] == 2)
    check("Stats win_rate ~66.7%", abs(stats["win_rate"] - 2/3) < 0.01, f"got {stats['win_rate']}")
    check("Stats avg_roi > 0", stats["avg_roi"] > 0, f"got {stats['avg_roi']}")

    # Unresolved should be empty
    check("No unresolved markets", len(db.get_unresolved_market_ids()) == 0)

    # Best/worst
    bw = db.get_best_worst(n=2)
    check("Best signal is s3 (ROI ~2.33)", bw["best"][0].transaction_hash == "tx_r3")
    check("Worst signal is s2 (ROI -1.0)", bw["worst"][0].transaction_hash == "tx_r2")

    Path(db_path).unlink(missing_ok=True)


# ── Test 3: Stats by Tier ────────────────────────────────────────────────

def test_stats_by_tier():
    print("\n🧪 Test 3: Stats by Likelihood Tier")

    db_path = tempfile.mktemp(suffix=".db")
    db = SignalDatabase(db_path)

    # Tier 0.4-0.6
    db.insert_signal(make_signal("m1", "tx_t1", likelihood=0.45, price=0.50, outcome="Yes"))
    db.insert_signal(make_signal("m1", "tx_t2", likelihood=0.55, price=0.40, outcome="No"))
    # Tier 0.6-0.8
    db.insert_signal(make_signal("m2", "tx_t3", likelihood=0.70, price=0.30, outcome="Yes"))
    # Tier 0.8-1.0
    db.insert_signal(make_signal("m3", "tx_t4", likelihood=0.90, price=0.20, outcome="Yes"))

    # Resolve
    db.mark_market_resolved("m1", "Yes", datetime.utcnow())
    db.mark_market_resolved("m2", "Yes", datetime.utcnow())
    db.mark_market_resolved("m3", "Yes", datetime.utcnow())

    tiers = db.get_stats_by_tier()
    check("3 tiers returned", len(tiers) == 3)

    tier_04 = next(t for t in tiers if t["tier"] == "0.4-0.6")
    check("Tier 0.4-0.6: total=2", tier_04["total"] == 2)
    check("Tier 0.4-0.6: correct=1 (Yes match)", tier_04["correct"] == 1)

    tier_06 = next(t for t in tiers if t["tier"] == "0.6-0.8")
    check("Tier 0.6-0.8: total=1, correct=1", tier_06["total"] == 1 and tier_06["correct"] == 1)

    tier_08 = next(t for t in tiers if t["tier"] == "0.8-1.0")
    check("Tier 0.8-1.0: total=1, correct=1", tier_08["total"] == 1 and tier_08["correct"] == 1)

    Path(db_path).unlink(missing_ok=True)


# ── Test 4: AnomalyHistoryService (SQLite backend) ───────────────────────

def test_anomaly_history_service():
    print("\n🧪 Test 4: AnomalyHistoryService (SQLite backend)")

    db_path = tempfile.mktemp(suffix=".db")
    svc = AnomalyHistoryService(db_path)

    # should_store_signal
    check("should_store 0.3 → False", not svc.should_store_signal(0.3))
    check("should_store 0.4 → True", svc.should_store_signal(0.4))

    # store_signal with low likelihood
    low_signal = make_signal("m1", "tx_low", likelihood=0.20)
    check("Low likelihood rejected", not svc.store_signal(low_signal))

    # store_signal with high likelihood
    high_signal = make_signal("m1", "tx_high", likelihood=0.65)
    check("High likelihood stored", svc.store_signal(high_signal))

    # Duplicate
    check("Duplicate rejected", not svc.store_signal(high_signal))

    # Retrieve
    signals = svc.get_signals_for_market("m1")
    check("get_signals_for_market returns 1", len(signals) == 1)

    # Format context
    ctx = svc.format_historical_signals_context(signals)
    check("Context contains header", "历史异常交易信号" in ctx)
    check("Context contains signal data", "$10,000" in ctx or "10,000" in ctx)

    # get_all_market_ids
    check("get_all_market_ids", svc.get_all_market_ids() == ["m1"])

    # get_signal_count
    check("get_signal_count", svc.get_signal_count("m1") == 1)

    Path(db_path).unlink(missing_ok=True)


# ── Test 5: JSON Migration ───────────────────────────────────────────────

def test_json_migration():
    print("\n🧪 Test 5: JSON → SQLite Migration")

    db_path = tempfile.mktemp(suffix=".db")
    db = SignalDatabase(db_path)

    # Migrate from actual anomaly_signals dir
    json_dir = Path(__file__).parent / "anomaly_signals"
    if json_dir.exists():
        count = db.migrate_from_json(json_dir)
        check(f"Migrated {count} signals from {json_dir}", count >= 0)

        # Verify data
        all_signals = db.get_all_signals(limit=100)
        check("Signals in DB after migration", len(all_signals) == count)

        for s in all_signals:
            check(
                f"  Signal {s.transaction_hash[:20]}... has market_question",
                len(s.market_question) > 0,
            )
    else:
        print("  ⏭️ No anomaly_signals/ dir found, skipping real migration test")

    # Test with synthetic JSON
    tmp_json_dir = Path(tempfile.mkdtemp())
    signals_data = [
        make_signal("m_json", "tx_json_1", likelihood=0.60).model_dump(mode="json"),
        make_signal("m_json", "tx_json_2", likelihood=0.75).model_dump(mode="json"),
    ]
    with open(tmp_json_dir / "m_json.json", "w") as f:
        json.dump(signals_data, f, default=str)

    db2_path = tempfile.mktemp(suffix=".db")
    db2 = SignalDatabase(db2_path)
    migrated = db2.migrate_from_json(tmp_json_dir)
    check("Synthetic migration: 2 signals", migrated == 2, f"got {migrated}")
    check("DB count after synthetic migration", db2.get_signal_count() == 2)

    # Cleanup
    shutil.rmtree(tmp_json_dir)
    Path(db_path).unlink(missing_ok=True)
    Path(db2_path).unlink(missing_ok=True)


# ── Test 6: StatsEngine format_stats_summary ─────────────────────────────

def test_stats_engine_summary():
    print("\n🧪 Test 6: StatsEngine format_stats_summary")

    db_path = tempfile.mktemp(suffix=".db")
    db = SignalDatabase(db_path)
    engine = StatsEngine(db)

    # No resolved signals → empty summary
    check("Empty summary when no resolved", engine.format_stats_summary() == "")

    # Add and resolve signals
    db.insert_signal(make_signal("m1", "tx_s1", outcome="Yes", price=0.30, likelihood=0.70))
    db.insert_signal(make_signal("m1", "tx_s2", outcome="No", price=0.50, likelihood=0.50))
    db.insert_signal(make_signal("m2", "tx_s3", outcome="No", price=0.25, likelihood=0.85))
    db.mark_market_resolved("m1", "Yes", datetime.utcnow())
    db.mark_market_resolved("m2", "No", datetime.utcnow())

    summary = engine.format_stats_summary()
    check("Summary contains header", "信号历史战绩" in summary)
    check("Summary contains win rate", "胜率" in summary)
    check("Summary contains ROI", "ROI" in summary)
    check("Summary contains tier breakdown", "按信号可信度分层" in summary)

    overview = engine.get_overview()
    check("Overview win_rate=2/3", abs(overview["win_rate"] - 2/3) < 0.01)
    check("Overview total_pnl > 0", overview["total_theoretical_pnl"] > 0)

    recent = engine.get_recent_resolved(limit=5)
    check("get_recent_resolved returns 3", len(recent) == 3)

    Path(db_path).unlink(missing_ok=True)


# ── Test 7: Resolution Tracker (mock API) ────────────────────────────────

def test_resolution_tracker():
    print("\n🧪 Test 7: ResolutionTracker (with live Gamma API)")

    db_path = tempfile.mktemp(suffix=".db")
    db = SignalDatabase(db_path)

    # Insert signals for the real markets from anomaly_signals/
    db.insert_signal(make_signal(
        "1640919", "tx_rt1",
        outcome="Yes", price=0.65, likelihood=0.45,
        question="US forces enter Iran by April 30?",
    ))
    db.insert_signal(make_signal(
        "1466016", "tx_rt2",
        outcome="No", price=0.40, likelihood=0.52,
        question="US x Iran ceasefire by April 30?",
    ))

    tracker = ResolutionTracker(db)

    # Run check — this hits the real Gamma API
    result = asyncio.run(tracker.check_all())
    check("check_all returned result", isinstance(result, dict))
    check("checked >= 1", result["checked"] >= 1, f"checked={result['checked']}")
    print(f"  ℹ️  Result: checked={result['checked']}, resolved={result['resolved']}, updated={result['signals_updated']}")

    # If markets are still open, they should remain unresolved
    # If resolved, signals should be updated
    stats = db.get_stats()
    print(f"  ℹ️  Stats after check: resolved={stats['resolved']}, correct={stats['correct']}")

    if result["resolved"] > 0:
        check("Signals updated after resolution", result["signals_updated"] > 0)
        resolved_signals = db.get_recent_resolved(limit=10)
        for s in resolved_signals:
            check(
                f"  Resolved signal has outcome: {s.resolved_outcome}",
                s.resolved_outcome is not None,
            )
            check(
                f"  Resolved signal has ROI: {s.theoretical_roi}",
                s.theoretical_roi is not None,
            )
    else:
        print("  ℹ️  Markets not yet resolved (expected for future-dated markets)")

    Path(db_path).unlink(missing_ok=True)


# ── Test 8: Dashboard API ────────────────────────────────────────────────

def test_dashboard_api():
    print("\n🧪 Test 8: Dashboard API")

    try:
        from fastapi.testclient import TestClient
    except ImportError:
        print("  ⏭️ fastapi not installed, skipping dashboard test")
        return

    # Populate the real DB with test data
    from src.config import get_settings
    settings = get_settings()
    db = SignalDatabase(settings.db_path)

    from src.dashboard import app
    client = TestClient(app)

    # GET /api/stats
    resp = client.get("/api/stats")
    check("/api/stats returns 200", resp.status_code == 200)
    data = resp.json()
    check("/api/stats has total_signals", "total_signals" in data)
    check("/api/stats has win_rate", "win_rate" in data)

    # GET /api/stats/tiers
    resp = client.get("/api/stats/tiers")
    check("/api/stats/tiers returns 200", resp.status_code == 200)
    tiers = resp.json()
    check("/api/stats/tiers is list", isinstance(tiers, list))

    # GET /api/signals
    resp = client.get("/api/signals?limit=10")
    check("/api/signals returns 200", resp.status_code == 200)
    signals = resp.json()
    check("/api/signals is list", isinstance(signals, list))

    # GET /api/signals/best-worst
    resp = client.get("/api/signals/best-worst?n=3")
    check("/api/signals/best-worst returns 200", resp.status_code == 200)
    bw = resp.json()
    check("/api/signals/best-worst has best/worst", "best" in bw and "worst" in bw)

    # GET / (HTML dashboard)
    resp = client.get("/")
    check("/ returns 200 HTML", resp.status_code == 200 and "<!DOCTYPE html>" in resp.text)

    print(f"  ℹ️  Dashboard stats: {data}")


# ── Test 9: Cleanup old signals ──────────────────────────────────────────

def test_cleanup():
    print("\n🧪 Test 9: Cleanup old signals")

    db_path = tempfile.mktemp(suffix=".db")
    db = SignalDatabase(db_path)

    # Insert old signal (40 days ago)
    old = make_signal("m_old", "tx_old", detected_hours_ago=40*24)
    db.insert_signal(old)

    # Insert recent signal
    recent = make_signal("m_new", "tx_new", detected_hours_ago=1)
    db.insert_signal(recent)

    check("Before cleanup: 2 signals", db.get_signal_count() == 2)

    removed = db.cleanup_old_signals(max_age_days=30)
    check("Removed 1 old signal", removed == 1, f"removed {removed}")
    check("After cleanup: 1 signal", db.get_signal_count() == 1)

    remaining = db.get_all_signals()
    check("Remaining is the recent one", remaining[0].transaction_hash == "tx_new")

    Path(db_path).unlink(missing_ok=True)


# ── Run all ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("信号战绩追踪系统 — 端到端测试")
    print("=" * 60)

    test_database_crud()
    test_resolution()
    test_stats_by_tier()
    test_anomaly_history_service()
    test_json_migration()
    test_stats_engine_summary()
    test_resolution_tracker()
    test_dashboard_api()
    test_cleanup()

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        exit(1)
