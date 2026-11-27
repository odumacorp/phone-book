"""The public website at /: real links, the facts it promises, and the web app at /app."""
import re


def test_landing_links_go_to_real_routes(client):
    html = client.get("/").get_data(as_text=True)
    assert 'lang="en-KE"' in html
    assert 'href="/app?start=register"' in html and 'href="/app"' in html and 'href="/platform/"' in html
    assert 'href="tel:*384*88041%23"' in html  # the # must be encoded or phones drop it
    assert 'href="/android"' in html
    for path in set(re.findall(r'(?:src|href)="(/site/[^"?]+)"', html)) | set(re.findall(r'(/site/img/[\w.-]+\.webp) \d+w', html)):
        assert client.get(path).status_code == 200, path


def test_landing_states_facts_and_nothing_about_funding(client):
    html = client.get("/").get_data(as_text=True)
    for fact in ["KES 50", "KES 99", "KES 249", "KES 2,500", "KES 7,500", "KES 20,000", "KES 30", "KES 25 ", "KES 20 per",
                 "KES 5,000", "KES 3,000", "KES 25,000", "KES 15,000", "999", "1199", "1195", "116", "odumacorp@gmail.com"]:
        assert fact in html, fact
    lower = html.lower()
    for word in ["investor", "funding", "fundraising", "most popular", "testimonial"]:
        assert word not in lower, word


def test_sector_photos_and_placeholders(client):
    html = client.get("/").get_data(as_text=True)
    for name in ("school", "sacco", "company"):
        assert f'src="/site/img/photos/{name}.webp"' in html
        assert client.get(f"/site/img/photos/{name}-640.webp").status_code == 200
    assert html.count("Photo to come") <= 1  # the "How it works" photo shows once its file is in site/img/photos/


def test_web_app_opens_on_register(client):
    assert client.get("/app/").status_code == 200
    assert b"/app" in client.get("/help").data  # Help's "Open the app" points at the app, not the website


def test_landing_is_gzipped_for_browsers_that_accept_it(client):
    import gzip
    resp = client.get("/", headers={"Accept-Encoding": "gzip, br"})
    assert resp.headers["Content-Encoding"] == "gzip" and b"Remote Phonebook" in gzip.decompress(resp.data)
    assert len(resp.data) < 30_000
