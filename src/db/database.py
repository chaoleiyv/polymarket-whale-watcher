"""SQLite database for anomaly signal storage and resolution tracking."""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.models.anomaly_signal import AnomalySignal
from src.models.trade import TraderRanking, TraderHistory

logger = logging.getLogger(__name__)


class SignalDatabase:
    """SQLite-backed storage for anomaly signals with resolution tracking."""

    def __init__(self, db_path: str = "data/signals.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            # Migrate: rename old column if it exists
            try:
                conn.execute(
                    "ALTER TABLE signals RENAME COLUMN insider_trading_likelihood TO information_asymmetry_score"
                )
                logger.info("Migrated column: insider_trading_likelihood -> information_asymmetry_score")
            except Exception:
                pass  # Column already renamed or table doesn't exist yet

            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT,
                    market_id TEXT NOT NULL,
                    market_question TEXT NOT NULL,
                    market_slug TEXT,
                    condition_id TEXT,
                    transaction_hash TEXT UNIQUE NOT NULL,
                    trade_timestamp INTEGER NOT NULL,
                    trade_side TEXT NOT NULL,
                    trade_price REAL NOT NULL,
                    trade_size_usd REAL NOT NULL,
                    trade_outcome TEXT NOT NULL,
                    trader_wallet TEXT,
                    trader_ranking_json TEXT,
                    trader_history_json TEXT,
                    information_asymmetry_score REAL NOT NULL DEFAULT 0.0,
                    reasoning TEXT DEFAULT '',
                    insider_evidence TEXT DEFAULT '',
                    detected_at TEXT NOT NULL,
                    market_resolved INTEGER DEFAULT 0,
                    market_resolved_at TEXT,
                    resolved_outcome TEXT,
                    signal_correct INTEGER,
                    theoretical_roi REAL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_market_id
                ON signals(market_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_market_resolved
                ON signals(market_resolved)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_likelihood
                ON signals(information_asymmetry_score DESC)
            """)

    def insert_signal(self, signal: AnomalySignal) -> bool:
        """Insert a signal, deduplicating by transaction_hash. Returns True if inserted."""
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO signals (
                        id, market_id, market_question, market_slug, condition_id,
                        transaction_hash, trade_timestamp, trade_side, trade_price,
                        trade_size_usd, trade_outcome, trader_wallet,
                        trader_ranking_json, trader_history_json,
                        information_asymmetry_score, reasoning, insider_evidence,
                        detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    signal.id,
                    signal.market_id,
                    signal.market_question,
                    signal.market_slug,
                    signal.condition_id,
                    signal.transaction_hash,
                    signal.trade_timestamp,
                    signal.trade_side,
                    signal.trade_price,
                    signal.trade_size_usd,
                    signal.trade_outcome,
                    signal.trader_wallet,
                    signal.trader_ranking.model_dump_json() if signal.trader_ranking else None,
                    signal.trader_history.model_dump_json() if signal.trader_history else None,
                    signal.information_asymmetry_score,
                    signal.reasoning,
                    signal.insider_evidence,
                    signal.detected_at.isoformat(),
                ))
                return conn.total_changes > 0
        except sqlite3.IntegrityError:
            logger.debug(f"Signal already exists: {signal.transaction_hash}")
            return False
        except Exception as e:
            logger.error(f"Failed to insert signal: {e}")
            return False

    def _row_to_signal(self, row: sqlite3.Row) -> AnomalySignal:
        """Convert a database row to an AnomalySignal."""
        trader_ranking = None
        if row["trader_ranking_json"]:
            try:
                trader_ranking = TraderRanking.model_validate_json(row["trader_ranking_json"])
            except Exception:
                pass

        trader_history = None
        if row["trader_history_json"]:
            try:
                trader_history = TraderHistory.model_validate_json(row["trader_history_json"])
            except Exception:
                pass

        return AnomalySignal(
            id=row["id"] or "",
            market_id=row["market_id"],
            market_question=row["market_question"],
            market_slug=row["market_slug"],
            condition_id=row["condition_id"],
            transaction_hash=row["transaction_hash"],
            trade_timestamp=row["trade_timestamp"],
            trade_side=row["trade_side"],
            trade_price=row["trade_price"],
            trade_size_usd=row["trade_size_usd"],
            trade_outcome=row["trade_outcome"],
            trader_wallet=row["trader_wallet"],
            trader_ranking=trader_ranking,
            trader_history=trader_history,
            information_asymmetry_score=row["information_asymmetry_score"],
            reasoning=row["reasoning"] or "",
            insider_evidence=row["insider_evidence"] or "",
            detected_at=datetime.fromisoformat(row["detected_at"]),
            market_resolved=bool(row["market_resolved"]),
            market_resolved_at=(
                datetime.fromisoformat(row["market_resolved_at"])
                if row["market_resolved_at"] else None
            ),
            resolved_outcome=row["resolved_outcome"],
            signal_correct=bool(row["signal_correct"]) if row["signal_correct"] is not None else None,
            theoretical_roi=row["theoretical_roi"],
        )

    def get_signals_for_market(
        self,
        market_id: str,
        top_recent: int = 5,
        top_likelihood: int = 5,
        min_likelihood: float = 0.4,
    ) -> List[AnomalySignal]:
        """Get top recent + top likelihood signals for a market, deduplicated.
        Only returns signals with likelihood >= min_likelihood (for LLM context)."""
        with self._get_conn() as conn:
            # Top recent (above threshold only)
            recent_rows = conn.execute(
                "SELECT * FROM signals WHERE market_id = ? AND information_asymmetry_score >= ? ORDER BY trade_timestamp DESC LIMIT ?",
                (market_id, min_likelihood, top_recent),
            ).fetchall()

            # Top likelihood (above threshold only)
            likelihood_rows = conn.execute(
                "SELECT * FROM signals WHERE market_id = ? AND information_asymmetry_score >= ? ORDER BY information_asymmetry_score DESC LIMIT ?",
                (market_id, min_likelihood, top_likelihood),
            ).fetchall()

        seen = set()
        combined = []
        for row in list(recent_rows) + list(likelihood_rows):
            tx_hash = row["transaction_hash"]
            if tx_hash not in seen:
                seen.add(tx_hash)
                combined.append(self._row_to_signal(row))

        combined.sort(key=lambda s: s.trade_timestamp, reverse=True)
        return combined

    def get_unresolved_market_ids(self) -> List[str]:
        """Return distinct market_ids that have unresolved signals."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT market_id FROM signals WHERE market_resolved = 0"
            ).fetchall()
        return [row["market_id"] for row in rows]

    def mark_market_resolved(
        self,
        market_id: str,
        resolved_outcome: str,
        resolved_at: datetime,
    ) -> int:
        """
        Mark all signals for a market as resolved and compute correctness/ROI.
        Returns number of updated rows.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT transaction_hash, trade_outcome, trade_price FROM signals WHERE market_id = ? AND market_resolved = 0",
                (market_id,),
            ).fetchall()

            updated = 0
            for row in rows:
                correct = row["trade_outcome"] == resolved_outcome
                if correct:
                    roi = (1.0 - row["trade_price"]) / row["trade_price"] if row["trade_price"] > 0 else 0.0
                else:
                    roi = -1.0

                conn.execute("""
                    UPDATE signals SET
                        market_resolved = 1,
                        market_resolved_at = ?,
                        resolved_outcome = ?,
                        signal_correct = ?,
                        theoretical_roi = ?
                    WHERE transaction_hash = ?
                """, (
                    resolved_at.isoformat(),
                    resolved_outcome,
                    int(correct),
                    roi,
                    row["transaction_hash"],
                ))
                updated += 1

            return updated

    def get_stats(self) -> dict:
        """Aggregate statistics: total, resolved, correct, win_rate, avg_roi."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN market_resolved = 1 THEN 1 ELSE 0 END) as resolved,
                    SUM(CASE WHEN signal_correct = 1 THEN 1 ELSE 0 END) as correct,
                    AVG(CASE WHEN market_resolved = 1 THEN theoretical_roi END) as avg_roi,
                    SUM(CASE WHEN market_resolved = 1 THEN theoretical_roi ELSE 0 END) as total_pnl
                FROM signals
            """).fetchone()

        total = row["total"]
        resolved = row["resolved"] or 0
        correct = row["correct"] or 0
        win_rate = correct / resolved if resolved > 0 else 0.0

        return {
            "total_signals": total,
            "resolved": resolved,
            "correct": correct,
            "win_rate": win_rate,
            "avg_roi": row["avg_roi"] or 0.0,
            "total_theoretical_pnl": row["total_pnl"] or 0.0,
        }

    def get_stats_by_tier(self) -> List[dict]:
        """Stats grouped by information_asymmetry_score tiers."""
        tiers = [
            ("0.4-0.6", 0.4, 0.6),
            ("0.6-0.8", 0.6, 0.8),
            ("0.8-1.0", 0.8, 1.01),
        ]
        results = []
        with self._get_conn() as conn:
            for label, low, high in tiers:
                row = conn.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN market_resolved = 1 THEN 1 ELSE 0 END) as resolved,
                        SUM(CASE WHEN signal_correct = 1 THEN 1 ELSE 0 END) as correct,
                        AVG(CASE WHEN market_resolved = 1 THEN theoretical_roi END) as avg_roi
                    FROM signals
                    WHERE information_asymmetry_score >= ? AND information_asymmetry_score < ?
                """, (low, high)).fetchone()

                resolved = row["resolved"] or 0
                correct = row["correct"] or 0
                results.append({
                    "tier": label,
                    "total": row["total"],
                    "resolved": resolved,
                    "correct": correct,
                    "win_rate": correct / resolved if resolved > 0 else 0.0,
                    "avg_roi": row["avg_roi"] or 0.0,
                })

        return results

    def get_all_signals(self, limit: int = 50, offset: int = 0) -> List[AnomalySignal]:
        """Paginated query of all signals, newest first."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY detected_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def get_all_market_ids(self) -> List[str]:
        """Get all distinct market IDs."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT DISTINCT market_id FROM signals").fetchall()
        return [row["market_id"] for row in rows]

    def get_signal_count(self, market_id: Optional[str] = None) -> int:
        """Count signals, optionally filtered by market_id."""
        with self._get_conn() as conn:
            if market_id:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM signals WHERE market_id = ?", (market_id,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) as cnt FROM signals").fetchone()
        return row["cnt"]

    def cleanup_old_signals(self, max_age_days: int = 30) -> int:
        """Remove signals older than max_age_days. Returns count removed."""
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM signals WHERE detected_at < ?", (cutoff,)
            )
            return cursor.rowcount

    def get_recent_resolved(self, limit: int = 20) -> List[AnomalySignal]:
        """Get recently resolved signals."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE market_resolved = 1 ORDER BY market_resolved_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def get_best_worst(self, n: int = 5) -> dict:
        """Get best and worst signals by ROI."""
        with self._get_conn() as conn:
            best_rows = conn.execute(
                "SELECT * FROM signals WHERE market_resolved = 1 ORDER BY theoretical_roi DESC LIMIT ?",
                (n,),
            ).fetchall()
            worst_rows = conn.execute(
                "SELECT * FROM signals WHERE market_resolved = 1 ORDER BY theoretical_roi ASC LIMIT ?",
                (n,),
            ).fetchall()
        return {
            "best": [self._row_to_signal(r) for r in best_rows],
            "worst": [self._row_to_signal(r) for r in worst_rows],
        }

    def migrate_from_json(self, json_dir: Path) -> int:
        """One-time migration from JSON files to SQLite. Returns count migrated."""
        if not json_dir.exists():
            logger.warning(f"JSON directory not found: {json_dir}")
            return 0

        count = 0
        for json_file in json_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                signals = data if isinstance(data, list) else data.get("signals", [])
                for item in signals:
                    try:
                        signal = AnomalySignal.model_validate(item)
                        if self.insert_signal(signal):
                            count += 1
                    except Exception as e:
                        logger.warning(f"Failed to parse signal from {json_file.name}: {e}")

            except Exception as e:
                logger.error(f"Failed to load {json_file}: {e}")

        logger.info(f"Migrated {count} signals from JSON to SQLite")
        return count
