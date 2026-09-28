import pytest

from heart_of_the_swarm.tools.builtin import _ensure_public_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://user:password@example.com/",
    ],
)
async def test_document_urls_reject_non_public_destinations(url: str) -> None:
    with pytest.raises(ValueError):
        await _ensure_public_url(url)
