from email_extract import extract_emails, INTERNAL_DOMAINS


def test_extract_simple_email():
    assert extract_emails("Hola, contacto: foo@bar.com.") == ["foo@bar.com"]


def test_extract_multiple_emails_dedup_sorted():
    txt = "Buyer santiagolaparra@gmail.com y mabro96@gmail.com, de nuevo santiagolaparra@gmail.com."
    assert extract_emails(txt) == ["mabro96@gmail.com", "santiagolaparra@gmail.com"]


def test_extract_emails_lowercases():
    assert extract_emails("Foo@Bar.Com") == ["foo@bar.com"]


def test_extract_emails_strips_trailing_punct():
    assert extract_emails("Escríbeme a foo@bar.com, por favor.") == ["foo@bar.com"]


def test_extract_emails_empty_input():
    assert extract_emails("") == []
    assert extract_emails(None) == []


def test_extract_emails_ignores_invalid():
    assert extract_emails("user@localhost") == []
    assert extract_emails("foo @bar.com") == []


def test_extract_emails_filter_internal():
    txt = "Agente soporte@eldiario.es escribe a cliente@gmail.com"
    assert extract_emails(txt, exclude_domains=INTERNAL_DOMAINS) == ["cliente@gmail.com"]


def test_extract_emails_preserves_plus_dots_hyphens():
    txt = "first.last+tag@sub-domain.co.uk"
    assert extract_emails(txt) == ["first.last+tag@sub-domain.co.uk"]
