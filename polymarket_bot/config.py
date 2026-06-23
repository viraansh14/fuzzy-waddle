import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # API credentials
    api_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_PASSPHRASE", ""))
    private_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_PRIVATE_KEY", ""))

    # Endpoints
    clob_api_url: str = field(
        default_factory=lambda: os.getenv("CLOB_API_URL", "https://clob.polymarket.com")
    )
    gamma_api_url: str = field(
        default_factory=lambda: os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com")
    )

    # Risk management
    max_position_size_usdc: float = field(
        default_factory=lambda: float(os.getenv("MAX_POSITION_SIZE_USDC", "100"))
    )
    max_total_exposure_usdc: float = field(
        default_factory=lambda: float(os.getenv("MAX_TOTAL_EXPOSURE_USDC", "500"))
    )
    max_positions: int = field(
        default_factory=lambda: int(os.getenv("MAX_POSITIONS", "10"))
    )
    stop_loss_pct: float = field(
        default_factory=lambda: float(os.getenv("STOP_LOSS_PCT", "15"))
    )
    take_profit_pct: float = field(
        default_factory=lambda: float(os.getenv("TAKE_PROFIT_PCT", "40"))
    )
    # Capital-preservation circuit breaker: once cumulative realized losses
    # reach this many USDC, stop opening new positions. 0 disables the breaker.
    max_total_loss_usdc: float = field(
        default_factory=lambda: float(os.getenv("MAX_TOTAL_LOSS_USDC", "0"))
    )

    # Bot settings
    trade_loop_interval: int = field(
        default_factory=lambda: int(os.getenv("TRADE_LOOP_INTERVAL_SECONDS", "60"))
    )
    min_liquidity: float = field(
        default_factory=lambda: float(os.getenv("MIN_LIQUIDITY_USDC", "1000"))
    )
    min_volume: float = field(
        default_factory=lambda: float(os.getenv("MIN_VOLUME_USDC", "5000"))
    )
    dry_run: bool = field(
        default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true"
    )

    # News API
    news_api_key: str = field(
        default_factory=lambda: os.getenv("NEWS_API_KEY", "")
    )

    def validate(self):
        if not self.private_key:
            raise ValueError("POLYMARKET_PRIVATE_KEY is required")
        if not self.api_key:
            raise ValueError("POLYMARKET_API_KEY is required")
        if not self.api_secret:
            raise ValueError("POLYMARKET_API_SECRET is required")
        if not self.api_passphrase:
            raise ValueError("POLYMARKET_API_PASSPHRASE is required")
        self.validate_risk_params()

    def validate_risk_params(self):
        """Validate the risk/sizing parameters independently of credentials so
        misconfiguration is caught before any trading begins."""
        if not 0 < self.stop_loss_pct <= 100:
            raise ValueError("STOP_LOSS_PCT must be in (0, 100]")
        if self.take_profit_pct <= 0:
            raise ValueError("TAKE_PROFIT_PCT must be positive")
        if self.max_position_size_usdc <= 0:
            raise ValueError("MAX_POSITION_SIZE_USDC must be positive")
        if self.max_total_exposure_usdc <= 0:
            raise ValueError("MAX_TOTAL_EXPOSURE_USDC must be positive")
        if self.max_position_size_usdc > self.max_total_exposure_usdc:
            raise ValueError(
                "MAX_POSITION_SIZE_USDC cannot exceed MAX_TOTAL_EXPOSURE_USDC"
            )
        if self.max_positions < 1:
            raise ValueError("MAX_POSITIONS must be at least 1")
        if self.max_total_loss_usdc < 0:
            raise ValueError("MAX_TOTAL_LOSS_USDC must be non-negative (0 disables it)")
