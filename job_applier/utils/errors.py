class JobApplierError(Exception):
    pass


class CVParseError(JobApplierError):
    pass


class AuthenticationError(JobApplierError):
    pass


class ApplicationError(JobApplierError):
    pass


class BotDetectionError(ApplicationError):
    pass


class FormFillError(ApplicationError):
    pass


class SubmissionError(ApplicationError):
    pass


class ConfigError(JobApplierError):
    pass
