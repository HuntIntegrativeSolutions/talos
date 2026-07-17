"""
P6 deliverable-schema landing: direct unit-level proof that RT-06's
whole-dict json.dumps scan (_flatten_text) covers the new `document` field,
not just `summary`. This critic only runs when client_identifiers is not
None (rule-promotion-only guard in talos.graph.spine.deliverable_node), so
these are direct function calls rather than an end-to-end spine test --
that guard is a pre-existing, out-of-scope scope limit this landing does
not touch or claim to fix.
"""
from talos.critics.no_client_identifiers_in_shared import no_client_identifiers_in_shared


def test_document_field_is_scanned_for_client_identifiers():
    deliverable = {
        "summary": "Routine investigation notes",
        "document": "## Findings\n\nSaw traffic from host acme-plc1.local on the line.",
        "citations": [{"finding_id": "nexus-context", "status": "confirmed"}],
    }
    result = no_client_identifiers_in_shared(deliverable, client_identifiers=["acme-plc1"])
    assert result.passed is False
    assert "client-identifier leak" in result.reason


def test_document_field_clean_passes():
    deliverable = {
        "summary": "Routine investigation notes",
        "document": "## Findings\n\nNo issues found.",
        "citations": [{"finding_id": "nexus-context", "status": "confirmed"}],
    }
    result = no_client_identifiers_in_shared(deliverable, client_identifiers=["acme-plc1"])
    assert result.passed is True
