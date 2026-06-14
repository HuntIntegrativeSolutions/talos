from platform.critics.citations_resolvable import citations_resolvable


def test_confirmed_citation_passes():
    deliverable = {"citations": [{"finding_id": "TAG-001", "status": "confirmed"}]}
    result = citations_resolvable(deliverable, nexus_client=None)
    assert result.passed is True


def test_proposed_citation_blocks():
    deliverable = {"citations": [{"finding_id": "TAG-001", "status": "proposed"}]}
    result = citations_resolvable(deliverable, nexus_client=None)
    assert result.passed is False
    assert "proposed" in result.reason


def test_nexus_unavailable_fails_closed():
    def bad_client(citations):
        raise ConnectionError("timeout")

    deliverable = {"citations": [{"finding_id": "TAG-001", "status": "confirmed"}]}
    result = citations_resolvable(deliverable, nexus_client=bad_client)
    assert result.passed is False
    assert "fail closed" in result.reason


def test_no_citations_fails():
    result = citations_resolvable({}, nexus_client=None)
    assert result.passed is False
    assert "no citations" in result.reason
