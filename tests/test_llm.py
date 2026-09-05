from sdgen.llm import default_deployment


def test_default_deployment_prefers_a_reasoning_model():
    assert default_deployment(["gpt-4.1", "gpt-5", "gpt-4o-mini"]) == "gpt-5"
    assert default_deployment(["gpt-4.1", "o3-mini"]) == "o3-mini"
    assert default_deployment(["gpt-4.1", "gpt-4o-mini"]) == "gpt-4.1"
    assert default_deployment([]) == ""
