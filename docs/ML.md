# Machine learning

## Data

* **Snapshots** (`token_snapshots`) are taken 1, 3, 5, 10 and 15 minutes after launch and every 60 s
  while a token is tracked. After evaluation each snapshot stores its point-in-time feature vector
  (`features`, 62 features listed in `backend/ml/features.py`). Unavailable values are NaN, never 0.
* **Labels** (`training_samples`) are created by `backend/ml/labeler.py` once the horizon
  (`SCANNER_OUTCOME_HORIZON_MINUTES`, 60) has passed, using only trades and snapshots *after* the
  snapshot time: max/min/close return, `hit_10/15/20` (net of `ML_FEE_AND_SLIPPAGE_PCT`), stop hit
  before target, time-to-target, rugged / liquidity removed / dead. Dead tokens (no trades after the
  snapshot) are labelled −100 % with `dead=true` – an unsellable position – and kept as negatives.
  Snapshots whose token was not tracked for the full horizon are not labelled (truncated outcomes).
* Rejected, failed and abandoned tokens are included: every tracked token produces snapshots.

## Models (`backend/ml/models.py`)

| Model | Type | Target |
|---|---|---|
| `rug_risk` | XGBoost classifier | rugged ∨ liquidity removed ∨ dead within the horizon |
| `breakout` | 3 XGBoost classifiers | hit +10 / +15 / +20 % net |
| `expected_return` | XGBoost regressors + classifier | close return, worst drawdown, time to +10 % (hits only), P(stop) |

Gradient boosting is the baseline; deep learning is intentionally absent.

## Validation

* Folds are chronological and grouped by token (a token's snapshots never straddle folds).
* Walk-forward: train on folds `< k`, test on fold `k`; metrics aggregated over out-of-sample
  predictions: AUC, precision/recall at the operating threshold, Brier score, log-loss, expected value
  of the selected trades, max drawdown of their cumulative return.
* Platt calibration is fitted on out-of-sample probabilities.
* Models are **experimental** until `ML_EXPERIMENTAL_UNTIL_SAMPLES` labelled samples exist and
  out-of-sample AUC ≥ 0.6 with at least `ML_MIN_POSITIVE_SAMPLES` positives. Experimental predictions
  are shrunk toward a neutral prior in ranking, flagged in the API/UI/GPT context and cannot cause hard
  rejections.
* Before any model exists the `HeuristicPredictor` (`heuristic-v0`) provides transparent rule-based
  estimates, always flagged experimental.

## Versioning and deployment

* `train_and_store` saves each model group as a `model_versions` row with metrics, feature names and the
  joblib artifact (stored in PostgreSQL – no volume needed). A new version is activated only if its
  headline metric is at least as good as the active one (`is_better`).
* The scanner's `MLScheduler` labels every minute and retrains every `ML_RETRAIN_INTERVAL_HOURS`
  (Redis lock prevents concurrent training). Manual: `python -m backend.ml.train [--activate]`.
* `/api/v1/status/models` lists versions and metrics; `candidate_scores.model_version` records which
  version scored each candidate so performance can be compared per version.

## Honest caveats

* Synthetic data in the test-suite only exercises code paths; it says nothing about real performance.
* Labels depend on observed trades; thin tokens produce noisy outcomes.
* Survivorship and regime drift are real: retraining is periodic and metrics are tracked per version.
