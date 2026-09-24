class TerraWatchError(Exception):
    """Base domain error for TerraWatch application logic."""

    status_code = 400


class NoAOIError(TerraWatchError):
    status_code = 404


class AOIPersistenceError(TerraWatchError):
    status_code = 500


class AOIOutsideKarnatakaError(TerraWatchError):
    status_code = 422


class GeographicDomainError(TerraWatchError):
    status_code = 422


class NoSuitableImageryError(TerraWatchError):
    status_code = 404


class CatalogFailureError(TerraWatchError):
    status_code = 502


class AssetAcquisitionError(TerraWatchError):
    status_code = 502


class RasterValidationError(TerraWatchError):
    status_code = 422


class NoAcquisitionError(TerraWatchError):
    status_code = 404


class MissingBeforeAcquisitionError(TerraWatchError):
    status_code = 404


class MissingAfterAcquisitionError(TerraWatchError):
    status_code = 404


class IncompatibleAcquisitionsError(TerraWatchError):
    status_code = 422


class DetectionRasterError(TerraWatchError):
    status_code = 422


class DetectionFailureError(TerraWatchError):
    status_code = 500


class NoTemporalObservationsError(TerraWatchError):
    status_code = 404


class InsufficientTemporalHistoryError(TerraWatchError):
    status_code = 422


class InvalidTemporalObservationsError(TerraWatchError):
    status_code = 422


class MissingDetectionError(TerraWatchError):
    status_code = 404


class TemporalAnalysisFailureError(TerraWatchError):
    status_code = 500


class MissingTemporalAnalysisError(TerraWatchError):
    status_code = 404


class CandidateNotFoundError(TerraWatchError):
    status_code = 404


class CandidateAnalysisUnavailableError(TerraWatchError):
    status_code = 422


class CandidateUpstreamFailureError(TerraWatchError):
    status_code = 502


class CandidateTriageFailureError(TerraWatchError):
    status_code = 500


class InvalidCandidateStateError(TerraWatchError):
    status_code = 422


class CandidateEvidenceError(TerraWatchError):
    status_code = 502


class EvidenceUnavailableError(TerraWatchError):
    status_code = 404


class EvidenceRasterError(TerraWatchError):
    status_code = 422


class DisplayImageryUnavailableError(TerraWatchError):
    status_code = 404


class DisplayImageryError(TerraWatchError):
    status_code = 422
