from nanobot.agent.context import ContextBuilder


def test_build_user_content_includes_image_and_pdf(tmp_path) -> None:
    cb = ContextBuilder(tmp_path)

    img = tmp_path / "a.png"
    img.write_bytes(b"not-a-real-png")

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\nnot-a-real-pdf")

    content = cb._build_user_content("hello", [str(img), str(pdf)])
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "hello"}

    assert any(p.get("type") == "image_url" for p in content)
    assert any(
        p.get("type") == "file"
        and isinstance(p.get("file"), dict)
        and p["file"].get("filename") == "doc.pdf"
        and str(p["file"].get("file_data") or "").startswith("data:application/pdf;base64,")
        for p in content
    )


def test_build_user_content_falls_back_to_text_when_no_supported_media(tmp_path) -> None:
    cb = ContextBuilder(tmp_path)
    txt = tmp_path / "note.txt"
    txt.write_text("hi", encoding="utf-8")

    content = cb._build_user_content("hello", [str(txt)])
    assert content == "hello"

