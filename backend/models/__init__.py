"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from backend.models.base import (
    AlertType,
    Base,
    Confidence,
    GptRecommendation,
    PoolKind,
    TokenStatus,
    TxType,
    WalletCategory,
    WhaleSignal,
    utcnow,
)
from backend.models.candidates import CandidateHistory, CandidateScore, GptAnalysis
from backend.models.intelligence import SmartMoneyEvent, TokenRiskEvent, WhaleEvent
from backend.models.ml import ModelPrediction, ModelVersion, TrainingSample
from backend.models.system import (
    AISpendDaily,
    Alert,
    ApplicationSetting,
    ProviderStatus,
    ProviderUsageDaily,
    RawEvent,
    ScannerCheckpoint,
    WorkerHeartbeat,
)
from backend.models.tokens import (
    HolderSnapshot,
    Token,
    TokenLaunch,
    TokenPool,
    TokenSnapshot,
    TokenTransaction,
)
from backend.models.trading import PaperTrade, TradeJournalEntry
from backend.models.wallets import (
    TrackedWallet,
    WalletProfile,
    WalletRelationship,
    WalletTokenPosition,
    WalletTransaction,
)

__all__ = [
    "AISpendDaily",
    "Alert",
    "AlertType",
    "ApplicationSetting",
    "Base",
    "CandidateHistory",
    "CandidateScore",
    "Confidence",
    "GptAnalysis",
    "GptRecommendation",
    "HolderSnapshot",
    "ModelPrediction",
    "ModelVersion",
    "PaperTrade",
    "PoolKind",
    "ProviderStatus",
    "ProviderUsageDaily",
    "RawEvent",
    "ScannerCheckpoint",
    "SmartMoneyEvent",
    "Token",
    "TokenLaunch",
    "TokenPool",
    "TokenRiskEvent",
    "TokenSnapshot",
    "TokenStatus",
    "TokenTransaction",
    "TradeJournalEntry",
    "TrackedWallet",
    "TrainingSample",
    "TxType",
    "WalletCategory",
    "WalletProfile",
    "WalletRelationship",
    "WalletTokenPosition",
    "WalletTransaction",
    "WhaleEvent",
    "WhaleSignal",
    "WorkerHeartbeat",
    "utcnow",
]
