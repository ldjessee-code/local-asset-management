from lam.jsonc import loads, strip_jsonc


def test_strips_line_and_block_comments():
    text = """
    {
      // greeting
      "a": "http://example.com", /* keep this string */
      "b": 1
    }
    """
    data = loads(text)
    assert data == {"a": "http://example.com", "b": 1}


def test_does_not_strip_slashes_inside_strings():
    raw = '{ "path": "C://temp/*not-a-comment*/file" }'
    assert "/*not-a-comment*/" in strip_jsonc(raw)
    assert loads(raw)["path"] == "C://temp/*not-a-comment*/file"
