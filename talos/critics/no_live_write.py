from talos.critics.citations_resolvable import CriticResult


def no_live_write_in_deliverable(deliverable: dict, nexus_client=None) -> CriticResult:
    """
    Safety critic (safety_class=True, waivable=False).
    Blocks any deliverable that contains a 'live_write' key set to True.
    In P2 this is a structural proof that safety critics exist and cannot be waived.
    In P6 this critic grows to cover write-profile tools.
    """
    if deliverable.get("live_write") is True:
        return CriticResult(
            passed=False,
            reason="deliverable contains live_write=True — safety critic blocks approval",
            waivable=False,
        )
    return CriticResult(passed=True, reason="no live write detected", waivable=False)
