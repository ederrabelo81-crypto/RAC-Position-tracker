"""
utils/adaptive_scraper.py — Sistema de melhoria contínua adaptativa para scrapers.

Este módulo implementa um "feedback loop" que aprende com cada coleta (sucesso ou 
falha) e ajusta automaticamente os parâmetros de scraping para maximizar a taxa de 
sucesso ao longo do tempo.

Por que existe
--------------
Cada plataforma (Magalu, Casas Bahia, Shopee, ML) tem diferentes níveis de proteção 
antibot em diferentes horários, dias da semana, condições de rede, etc. Um sistema 
rígido com parâmetros fixos sempre terá problemas quando as condições mudam.

Este módulo coleta métricas de cada execução e usa um algoritmo de bandido multi-
braço (multi-armed bandit) simples para ajustar:
  * Tempo de espera entre ações (simula humano)
  * Estratégia de coleta preferida (API vs Browser)
  * Timeout ideal para cada plataforma
  * Número máximo de páginas seguras
  * Horário ótimo de coleta

Como funciona
-------------
1. Cada coleta registra métricas no SQLite local (persistente)
2. Um score de sucesso é calculado por estratégia usada
3. O sistema recomenda a melhor estratégia para o contexto atual
4. Após N execuções, o sistema converge para os melhores parâmetros

Uso
---
Os scrapers consultam este módulo ANTES de iniciar para pegar os parâmetros ótimos,
e registram o resultado DEPOIS de cada keyword/coleta.

Exemplo:
    from utils.adaptive_scraper import AdaptiveScraperConfig
    
    config = AdaptiveScraperConfig.get_for_platform("Casas Bahia")
    wait_time = config.recommended_wait_time
    strategy = config.preferred_strategy
    
    # ... executa coleta ...
    
    AdaptiveScraperConfig.record_result(
        platform="Casas Bahia",
        strategy=strategy,
        success=True,
        items_collected=54,
        duration_seconds=45.2
    )
"""

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _PROJECT_ROOT / "data" / "adaptive_scraper.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Pesos para cálculo do score de sucesso
_WEIGHT_SUCCESS = 10.0          # Sucesso básico
_WEIGHT_ITEMS = 0.5             # Por item coletado (incentiva volume)
_WEIGHT_SPEED = 0.1             # Por item/segundo (eficiência)
_WEIGHT_FAIL = -20.0            # Falha total
_WEIGHT_PARTIAL = -5.0          # Sucesso parcial (< 10 itens)

# Mínimo de execuções por estratégia antes de considerar "estatisticamente válido"
_MIN_EXECUTIONS_PER_STRATEGY = 5

# Janela de tempo para análise (últimos dias)
_ANALYSIS_WINDOW_DAYS = 7

# Estratégias disponíveis
STRATEGY_API_ONLY = "api_only"           # Só API (curl_cffi/requests)
STRATEGY_BROWSER_FIRST = "browser_first" # Browser primeiro, API fallback
STRATEGY_HYBRID = "hybrid"               # API + browser em paralelo
STRATEGY_LOCAL_CHROME = "local_chrome"   # Chrome local logado (RAC_LOCAL_CHROME)

# Plataformas suportadas
SUPPORTED_PLATFORMS = ["Magalu", "Casas Bahia", "Shopee", "Mercado Livre", "Amazon", "Leroy Merlin"]


@dataclass
class ExecutionRecord:
    """Registro de uma execução de coleta."""
    id: Optional[int]
    timestamp: str
    platform: str
    strategy: str
    success: bool
    items_collected: int
    duration_seconds: float
    error_type: Optional[str]
    hour_of_day: int
    day_of_week: int
    pages_attempted: int
    pages_successful: int
    avg_wait_time_ms: int
    notes: Optional[str] = None


@dataclass
class StrategyStats:
    """Estatísticas de uma estratégia para uma plataforma."""
    strategy: str
    total_executions: int
    success_rate: float
    avg_items: float
    avg_duration: float
    efficiency_score: float  # Itens por segundo
    weighted_score: float    # Score ponderado para ranking
    last_used: Optional[str]
    recommended: bool = False


@dataclass
class AdaptiveConfig:
    """Configuração recomendada para uma plataforma."""
    platform: str
    preferred_strategy: str
    recommended_wait_time_ms: int
    recommended_timeout_sec: int
    recommended_max_pages: int
    confidence_level: float  # 0-1, quão confiante é a recomendação
    alternative_strategies: List[str] = field(default_factory=list)
    best_hour_range: Tuple[int, int] = (0, 24)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

def _init_db(conn: sqlite3.Connection) -> None:
    """Inicializa o schema do banco de dados."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            platform TEXT NOT NULL,
            strategy TEXT NOT NULL,
            success BOOLEAN NOT NULL,
            items_collected INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            error_type TEXT,
            hour_of_day INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            pages_attempted INTEGER DEFAULT 0,
            pages_successful INTEGER DEFAULT 0,
            avg_wait_time_ms INTEGER DEFAULT 0,
            notes TEXT
        );
        
        CREATE INDEX IF NOT EXISTS idx_platform_timestamp 
        ON executions(platform, timestamp);
        
        CREATE INDEX IF NOT EXISTS idx_strategy 
        ON executions(platform, strategy);
        
        -- Tabela de configuração dinâmica (ajustes manuais ou automáticos)
        CREATE TABLE IF NOT EXISTS platform_config (
            platform TEXT PRIMARY KEY,
            override_strategy TEXT,
            min_wait_time_ms INTEGER DEFAULT 500,
            max_wait_time_ms INTEGER DEFAULT 3000,
            timeout_sec INTEGER DEFAULT 45,
            max_pages INTEGER DEFAULT 5,
            enabled BOOLEAN DEFAULT 1,
            updated_at TEXT
        );
        
        -- Tabela de bloqueios de IP/WAF
        CREATE TABLE IF NOT EXISTS waf_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            platform TEXT NOT NULL,
            ip_type TEXT,  -- 'residential', 'datacenter', 'mobile'
            blocked BOOLEAN NOT NULL,
            strategy_used TEXT,
            recovery_method TEXT  -- Como foi recuperado
        );
    """)
    conn.commit()


def _get_connection() -> sqlite3.Connection:
    """Obtém conexão com o banco de dados."""
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Registro de execuções
# ---------------------------------------------------------------------------

def record_execution(
    platform: str,
    strategy: str,
    success: bool,
    items_collected: int,
    duration_seconds: float,
    error_type: Optional[str] = None,
    pages_attempted: int = 0,
    pages_successful: int = 0,
    avg_wait_time_ms: int = 1000,
    notes: Optional[str] = None
) -> int:
    """
    Registra o resultado de uma execução de coleta.
    
    Returns:
        ID do registro inserido.
    """
    now = datetime.now()
    record = ExecutionRecord(
        id=None,
        timestamp=now.isoformat(),
        platform=platform,
        strategy=strategy,
        success=success,
        items_collected=items_collected,
        duration_seconds=duration_seconds,
        error_type=error_type,
        hour_of_day=now.hour,
        day_of_week=now.weekday(),
        pages_attempted=pages_attempted,
        pages_successful=pages_successful,
        avg_wait_time_ms=avg_wait_time_ms,
        notes=notes
    )
    
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO executions (
                timestamp, platform, strategy, success, items_collected,
                duration_seconds, error_type, hour_of_day, day_of_week,
                pages_attempted, pages_successful, avg_wait_time_ms, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.timestamp, record.platform, record.strategy,
            record.success, record.items_collected, record.duration_seconds,
            record.error_type, record.hour_of_day, record.day_of_week,
            record.pages_attempted, record.pages_successful,
            record.avg_wait_time_ms, record.notes
        ))
        conn.commit()
        record_id = cursor.lastrowid
        
        # Log discreto
        status = "✓" if success else "✗"
        logger.debug(
            f"[AdaptiveScraper] {status} {platform} | {strategy} | "
            f"{items_collected} itens | {duration_seconds:.1f}s"
        )
        
        return record_id
    finally:
        conn.close()


def record_waf_event(
    platform: str,
    blocked: bool,
    ip_type: str = "residential",
    strategy_used: str = STRATEGY_LOCAL_CHROME,
    recovery_method: Optional[str] = None
) -> None:
    """Registra evento de bloqueio/recuperação do WAF."""
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO waf_events (timestamp, platform, ip_type, blocked, strategy_used, recovery_method)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), platform, ip_type, blocked, strategy_used, recovery_method))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Análise e recomendações
# ---------------------------------------------------------------------------

def _calculate_weighted_score(record: ExecutionRecord) -> float:
    """Calcula score ponderado para um registro."""
    if not record.success:
        return _WEIGHT_FAIL
    
    base_score = _WEIGHT_SUCCESS
    items_bonus = min(record.items_collected * _WEIGHT_ITEMS, 20)  # Cap em 20
    efficiency = (record.items_collected / max(record.duration_seconds, 1)) * _WEIGHT_SPEED
    
    if record.items_collected < 10:
        base_score += _WEIGHT_PARTIAL
    
    return base_score + items_bonus + efficiency


def get_strategy_stats(
    platform: str,
    days_window: int = _ANALYSIS_WINDOW_DAYS
) -> List[StrategyStats]:
    """
    Obtém estatísticas de todas as estratégias para uma plataforma.
    
    Args:
        platform: Nome da plataforma.
        days_window: Janela de dias para análise.
    
    Returns:
        Lista de StrategyStats ordenada por weighted_score.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cutoff_date = (datetime.now() - timedelta(days=days_window)).isoformat()
        
        cursor.execute("""
            SELECT 
                strategy,
                COUNT(*) as total_executions,
                AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as success_rate,
                AVG(items_collected) as avg_items,
                AVG(duration_seconds) as avg_duration,
                MAX(timestamp) as last_used
            FROM executions
            WHERE platform = ? AND timestamp >= ?
            GROUP BY strategy
            ORDER BY total_executions DESC
        """, (platform, cutoff_date))
        
        rows = cursor.fetchall()
        stats_list = []
        
        for row in rows:
            strategy = row['strategy']
            
            # Calcula eficiência e score ponderado
            cursor.execute("""
                SELECT success, items_collected, duration_seconds
                FROM executions
                WHERE platform = ? AND strategy = ? AND timestamp >= ?
            """, (platform, strategy, cutoff_date))
            
            exec_rows = cursor.fetchall()
            weighted_scores = []
            efficiencies = []
            
            for exec_row in exec_rows:
                record = ExecutionRecord(
                    id=None, timestamp="", platform=platform, strategy=strategy,
                    success=bool(exec_row['success']),
                    items_collected=exec_row['items_collected'] or 0,
                    duration_seconds=exec_row['duration_seconds'] or 1,
                    error_type=None, hour_of_day=0, day_of_week=0,
                    pages_attempted=0, pages_successful=0, avg_wait_time_ms=0
                )
                weighted_scores.append(_calculate_weighted_score(record))
                efficiencies.append(
                    record.items_collected / max(record.duration_seconds, 1)
                )
            
            avg_weighted_score = sum(weighted_scores) / len(weighted_scores) if weighted_scores else 0
            avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else 0
            
            stats = StrategyStats(
                strategy=strategy,
                total_executions=row['total_executions'],
                success_rate=row['success_rate'] or 0,
                avg_items=row['avg_items'] or 0,
                avg_duration=row['avg_duration'] or 0,
                efficiency_score=avg_efficiency,
                weighted_score=avg_weighted_score,
                last_used=row['last_used']
            )
            stats_list.append(stats)
        
        # Ordena por weighted_score
        stats_list.sort(key=lambda x: x.weighted_score, reverse=True)
        
        # Marca a melhor como recomendada (se tiver mínimo de execuções)
        if stats_list and stats_list[0].total_executions >= _MIN_EXECUTIONS_PER_STRATEGY:
            stats_list[0].recommended = True
        
        return stats_list
    finally:
        conn.close()


def get_adaptive_config(platform: str) -> AdaptiveConfig:
    """
    Obtém configuração adaptativa recomendada para uma plataforma.
    
    Args:
        platform: Nome da plataforma.
    
    Returns:
        AdaptiveConfig com as melhores práticas aprendidas.
    """
    stats = get_strategy_stats(platform)
    
    # Verifica configurações manuais (override)
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM platform_config WHERE platform = ?", 
            (platform,)
        )
        override = cursor.fetchone()
        
        if override and override['enabled']:
            # Configuração manual existe e está ativa
            return AdaptiveConfig(
                platform=platform,
                preferred_strategy=override['override_strategy'] or (stats[0].strategy if stats else STRATEGY_LOCAL_CHROME),
                recommended_wait_time_ms=(override['min_wait_time_ms'] + override['max_wait_time_ms']) // 2,
                recommended_timeout_sec=override['timeout_sec'],
                recommended_max_pages=override['max_pages'],
                confidence_level=1.0,  # Override manual = confiança máxima
                notes="Configuração manual aplicada"
            )
    finally:
        conn.close()
    
    # Sem override: usa aprendizado automático
    if not stats:
        # Sem dados históricos: retorna defaults conservadores
        return AdaptiveConfig(
            platform=platform,
            preferred_strategy=STRATEGY_LOCAL_CHROME,
            recommended_wait_time_ms=1500,
            recommended_timeout_sec=45,
            recommended_max_pages=3,
            confidence_level=0.3,
            notes="Sem dados históricos - usando defaults"
        )
    
    # Analisa melhor estratégia
    best = stats[0]
    confidence = min(best.total_executions / 20.0, 1.0)  # Normaliza 0-1
    
    # Calcula wait time recomendado baseado nas execuções bem-sucedidas
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT AVG(avg_wait_time_ms) as avg_wait
            FROM executions
            WHERE platform = ? AND strategy = ? AND success = 1
        """, (platform, best.strategy))
        row = cursor.fetchone()
        recommended_wait = int(row['avg_wait']) if row and row['avg_wait'] else 1500
        
        # Analisa melhor faixa de horário
        cursor.execute("""
            SELECT hour_of_day, AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as success_rate
            FROM executions
            WHERE platform = ?
            GROUP BY hour_of_day
            ORDER BY success_rate DESC
            LIMIT 1
        """, (platform,))
        hour_row = cursor.fetchone()
        best_hour = hour_row['hour_of_day'] if hour_row else 12
        
    finally:
        conn.close()
    
    # Estratégias alternativas (top 3)
    alternatives = [s.strategy for s in stats[1:4]]
    
    return AdaptiveConfig(
        platform=platform,
        preferred_strategy=best.strategy,
        recommended_wait_time_ms=max(500, min(recommended_wait, 3000)),
        recommended_timeout_sec=int(best.avg_duration / max(best.avg_items, 1)) + 30,
        recommended_max_pages=5,  # Pode ser refinado no futuro
        confidence_level=confidence,
        alternative_strategies=alternatives,
        best_hour_range=(max(0, best_hour - 2), min(23, best_hour + 2)),
        notes=f"Baseado em {best.total_executions} execuções ({best.success_rate*100:.0f}% sucesso)"
    )


# ---------------------------------------------------------------------------
# Interface para scrapers
# ---------------------------------------------------------------------------

class AdaptiveScraperConfig:
    """Interface simplificada para scrapers usarem o sistema adaptativo."""
    
    @staticmethod
    def get_for_platform(platform: str) -> AdaptiveConfig:
        """Obtém configuração recomendada para uma plataforma."""
        return get_adaptive_config(platform)
    
    @staticmethod
    def record_result(
        platform: str,
        strategy: str,
        success: bool,
        items_collected: int,
        duration_seconds: float,
        error_type: Optional[str] = None,
        pages_attempted: int = 0,
        pages_successful: int = 0,
        wait_time_ms: int = 1000,
        notes: Optional[str] = None
    ) -> None:
        """Registra resultado de uma coleta."""
        record_execution(
            platform=platform,
            strategy=strategy,
            success=success,
            items_collected=items_collected,
            duration_seconds=duration_seconds,
            error_type=error_type,
            pages_attempted=pages_attempted,
            pages_successful=pages_successful,
            avg_wait_time_ms=wait_time_ms,
            notes=notes
        )
    
    @staticmethod
    def record_waf_block(
        platform: str,
        ip_type: str = "residential",
        strategy_used: str = STRATEGY_LOCAL_CHROME,
        recovery_method: Optional[str] = None
    ) -> None:
        """Registra bloqueio do WAF."""
        record_waf_event(
            platform=platform,
            blocked=True,
            ip_type=ip_type,
            strategy_used=strategy_used,
            recovery_method=recovery_method
        )
    
    @staticmethod
    def set_manual_override(
        platform: str,
        strategy: Optional[str] = None,
        min_wait_time_ms: int = 500,
        max_wait_time_ms: int = 3000,
        timeout_sec: int = 45,
        max_pages: int = 5,
        enabled: bool = True
    ) -> None:
        """Define configuração manual para uma plataforma."""
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO platform_config 
                (platform, override_strategy, min_wait_time_ms, max_wait_time_ms, 
                 timeout_sec, max_pages, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (platform, strategy, min_wait_time_ms, max_wait_time_ms,
                  timeout_sec, max_pages, enabled, datetime.now().isoformat()))
            conn.commit()
            logger.info(f"[AdaptiveScraper] Override manual definido para {platform}")
        finally:
            conn.close()
    
    @staticmethod
    def clear_override(platform: str) -> None:
        """Remove configuração manual, volta ao aprendizado automático."""
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM platform_config WHERE platform = ?",
                (platform,)
            )
            conn.commit()
            logger.info(f"[AdaptiveScraper] Override removido para {platform}")
        finally:
            conn.close()
    
    @staticmethod
    def get_stats_summary(platform: str) -> str:
        """Retorna resumo em texto das estatísticas da plataforma."""
        stats = get_strategy_stats(platform)
        config = get_adaptive_config(platform)
        
        lines = [
            f"\n{'='*60}",
            f"RELATÓRIO ADAPTATIVO: {platform}",
            f"{'='*60}",
            f"Estratégia recomendada: {config.preferred_strategy}",
            f"Confiança: {config.confidence_level*100:.0f}%",
            f"Wait time recomendado: {config.recommended_wait_time_ms}ms",
            f"Timeout recomendado: {config.recommended_timeout_sec}s",
            f"Notas: {config.notes}",
            f"",
            f"DESEMPENHO POR ESTRATÉGIA:",
            f"{'-'*60}"
        ]
        
        for s in stats:
            rec_marker = " ★ RECOMENDADA" if s.recommended else ""
            lines.append(
                f"  {s.strategy}{rec_marker}\n"
                f"    Execuções: {s.total_executions}\n"
                f"    Sucesso: {s.success_rate*100:.1f}%\n"
                f"    Média itens: {s.avg_items:.1f}\n"
                f"    Score: {s.weighted_score:.2f}"
            )
        
        lines.append(f"{'='*60}\n")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI para análise
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        platform = sys.argv[1]
        if platform in SUPPORTED_PLATFORMS:
            print(AdaptiveScraperConfig.get_stats_summary(platform))
        else:
            print(f"Plataformas suportadas: {SUPPORTED_PLATFORMS}")
    else:
        print("Uso: python -m utils.adaptive_scraper <plataforma>")
        print(f"Plataformas: {', '.join(SUPPORTED_PLATFORMS)}")
        
        # Mostra resumo geral
        print("\n" + "="*60)
        print("VISÃO GERAL DO SISTEMA ADAPTATIVO")
        print("="*60)
        
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT platform, COUNT(*) as total, 
                       AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) as success_rate
                FROM executions
                GROUP BY platform
            """)
            for row in cursor.fetchall():
                print(f"  {row['platform']}: {row['total']} execuções, "
                      f"{row['success_rate']*100:.1f}% sucesso")
        finally:
            conn.close()
