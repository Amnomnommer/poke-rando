"""Alpha 0.1 build pipeline: preflight, disposable workroot, staging, patches, assembly, post-build, verification."""


class BuildError(RuntimeError):
    """The build cannot continue; nothing was written outside the workroot."""
