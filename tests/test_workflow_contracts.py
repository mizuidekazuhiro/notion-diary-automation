from scripts.workflow_contracts import check_workflow_chain


def test_workflow_contracts() -> None:
    assert check_workflow_chain() == []
