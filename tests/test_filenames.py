"""Filename rules for course and planner downloads."""

from smartschool_mcp.filenames import download_filename, extension_for_mime


def test_short_and_real_mime_types() -> None:
    assert extension_for_mime("pdf") == ".pdf"
    assert extension_for_mime("docx") == ".docx"
    assert extension_for_mime("application/pdf") == ".pdf"
    assert (
        extension_for_mime(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        == ".docx"
    )
    assert extension_for_mime("image/jpeg") == ".jpg"
    assert extension_for_mime("not-a-type") == ""


def test_content_disposition_wins_and_extensions_are_not_doubled() -> None:
    assert download_filename("werkblad", "pdf") == "werkblad.pdf"
    assert download_filename("werkblad.pdf", "pdf") == "werkblad.pdf"
    assert download_filename("werkblad.docx", "pdf") == "werkblad.docx"
    assert (
        download_filename("werkblad", "pdf", 'attachment; filename="opdracht.pdf"')
        == "opdracht.pdf"
    )
    assert (
        download_filename(
            "werkblad",
            "docx",
            "attachment; filename*=UTF-8''opdracht.docx",
        )
        == "opdracht.docx"
    )
    assert download_filename("werkblad", "pdf", 'attachment; filename="kaal"') == (
        "kaal.pdf"
    )
