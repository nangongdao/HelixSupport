import pytest

from app.labels import normalize_conversation_labels


def test_normalize_basic():
    result = normalize_conversation_labels(["Bug", "Feature", "urgent"])
    assert result == ["bug", "feature", "urgent"]


def test_normalize_strips_whitespace():
    result = normalize_conversation_labels(["  Bug  ", "Feature\t", "\nurgent "])
    assert result == ["bug", "feature", "urgent"]


def test_normalize_deduplicates_case_insensitive():
    result = normalize_conversation_labels(["Bug", "BUG", "bug", "Feature"])
    assert result == ["bug", "feature"]


def test_normalize_empty_labels_rejected():
    with pytest.raises(ValueError, match="labels must be printable"):
        normalize_conversation_labels(["Bug", "   ", "Feature"])


def test_normalize_too_long_rejected():
    long_label = "a" * 33
    with pytest.raises(ValueError, match="labels must be printable"):
        normalize_conversation_labels([long_label])


def test_normalize_non_printable_rejected():
    with pytest.raises(ValueError, match="labels must be printable"):
        normalize_conversation_labels(["Bug", "Feature\x00", "urgent"])


def test_normalize_max_labels_exceeded():
    labels = [f"label{i}" for i in range(21)]
    with pytest.raises(ValueError, match="a conversation can have at most 20 labels"):
        normalize_conversation_labels(labels)


def test_normalize_custom_max_labels():
    labels = ["a", "b", "c", "d"]
    with pytest.raises(ValueError, match="a conversation can have at most 3 labels"):
        normalize_conversation_labels(labels, max_labels=3)


def test_normalize_exactly_max_labels_allowed():
    labels = [f"label{i}" for i in range(20)]
    result = normalize_conversation_labels(labels)
    assert len(result) == 20


def test_normalize_empty_input():
    result = normalize_conversation_labels([])
    assert result == []


def test_normalize_max_length_allowed():
    label = "a" * 32
    result = normalize_conversation_labels([label])
    assert result == [label]
