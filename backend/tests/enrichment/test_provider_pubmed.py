"""Tests for academic enrichment providers and aggregation."""

import logging

import httpx
import pytest
from assertpy import assert_that

from podex.logging_config import configure_logging
from podex.models import Media, MediaType
from podex.services.enrichment import (
    PubMedProvider,
    SemanticScholarProvider,
)
from tests.enrichment.conftest import (
    _CountingLimiter,
    _media,
    _swap_client,
    enrich_result,
)


def test_pubmed_happy_path_via_esearch_and_efetch() -> None:
    """PubMed search ids resolve through the XML fetch into a result."""

    article_xml = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <ArticleTitle>Sleep and memory consolidation</ArticleTitle>
        <Abstract><AbstractText>A study of sleep.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author>
        </AuthorList>
        <Journal>
          <Title>Journal of Sleep</Title>
          <JournalIssue>
            <PubDate><Year>2020</Year></PubDate>
          </JournalIssue>
        </Journal>
        <ELocationID EIdType="doi">10.1000/example.doi</ELocationID>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""

    summary_payload = {
        "result": {
            "uids": ["12345"],
            "12345": {
                "uid": "12345",
                "title": "Sleep and memory consolidation",
                "pubdate": "2020",
                "fulljournalname": "Journal of Sleep",
                "authors": [{"name": "Doe J"}],
                "elocationid": "doi: 10.1000/example.doi",
            },
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "esearch" in request.url.path:
            return httpx.Response(
                200,
                json={"esearchresult": {"idlist": ["12345"]}},
            )
        if "esummary" in request.url.path:
            return httpx.Response(200, json=summary_payload)
        return httpx.Response(200, text=article_xml)

    provider = PubMedProvider()
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = enrich_result(
        provider, _media("Sleep and memory consolidation", MediaType.STUDY)
    )
    assert_that(result.has_useful_data()).is_true()
    assert_that(result.metadata["title"]).is_equal_to(
        "Sleep and memory consolidation",
    )


def test_pubmed_direct_pmid_fetch() -> None:
    """A stored pubmed_id skips search and fetches the article."""

    xml = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>777</PMID>
      <Article>
        <ArticleTitle>Direct fetch study</ArticleTitle>
        <Abstract><AbstractText>Direct.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author>
        </AuthorList>
        <Journal>
          <Title>Journal</Title>
          <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
        </Journal>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""
    provider = PubMedProvider()
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=xml),
        ),
    )
    media = _media("Direct fetch study", MediaType.STUDY)
    media.pubmed_id = "777"
    result = enrich_result(provider, media)
    assert_that(result.confidence).is_equal_to(1.0)
    assert_that(result.metadata["title"]).is_equal_to("Direct fetch study")


def test_pubmed_doi_search_path() -> None:
    """A media DOI resolves through the PubMed DOI search to an article."""

    xml = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>888</PMID>
      <Article>
        <ArticleTitle>DOI-resolved study</ArticleTitle>
        <Abstract><AbstractText>Resolved.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author>
        </AuthorList>
        <Journal>
          <Title>Journal</Title>
          <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
        </Journal>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "esearch" in request.url.path:
            return httpx.Response(
                200,
                json={"esearchresult": {"idlist": ["888"]}},
            )
        if "esummary" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "uids": ["888"],
                        "888": {
                            "uid": "888",
                            "title": "DOI-resolved study",
                            "pubdate": "2021",
                        },
                    },
                },
            )
        return httpx.Response(200, text=xml)

    provider = PubMedProvider(api_key="key")
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    media = _media("DOI-resolved study", MediaType.STUDY)
    media.doi = "10.1000/example.doi"
    result = enrich_result(provider, media)
    assert_that(result.has_useful_data()).is_true()


def test_pubmed_rich_article_parse() -> None:
    """Mesh terms, PMC ids, months, and DOIs parse from article XML."""

    xml = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>999</PMID>
      <Article>
        <ArticleTitle>Rich parse study</ArticleTitle>
        <Abstract><AbstractText>Rich.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author>
          <Author><CollectiveName>The Consortium</CollectiveName></Author>
        </AuthorList>
        <Journal>
          <Title>Journal</Title>
          <JournalIssue>
            <PubDate><Year>2021</Year><Month>06</Month></PubDate>
          </JournalIssue>
        </Journal>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName>Sleep</DescriptorName></MeshHeading>
        <MeshHeading><DescriptorName>Memory</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1000/rich.doi</ArticleId>
        <ArticleId IdType="pmc">PMC12345</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""
    provider = PubMedProvider()
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=xml),
        ),
    )
    media = _media("Rich parse study", MediaType.STUDY)
    media.pubmed_id = "999"
    result = enrich_result(provider, media)
    assert_that(str(result.metadata)).contains("Sleep")


def test_pubmed_author_scoped_search() -> None:
    """Author names scope the PubMed query with the [Author] field."""

    seen_terms: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        term = request.url.params.get("term", "")
        seen_terms.append(term)
        return httpx.Response(200, json={"esearchresult": {"idlist": []}})

    provider = PubMedProvider()
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    media = Media(
        type=MediaType.STUDY,
        title="Sleep and memory consolidation",
        author="Jane Doe and John Smith",
    )
    media.id = 6
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()
    assert_that(" ".join(seen_terms)).contains("[Author]")


def test_pubmed_and_semantic_scholar_reject_paths() -> None:
    """Unsupported types and title mismatches yield None."""

    pm = PubMedProvider()
    pm.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={}),
        ),
    )
    movie = Media(type=MediaType.MOVIE, title="Dune")
    movie.id = 13
    assert_that(pm.search_and_enrich(movie)).is_none()

    def mismatch_handler(request: httpx.Request) -> httpx.Response:
        if "esearch" in request.url.path:
            return httpx.Response(
                200,
                json={"esearchresult": {"idlist": ["555"]}},
            )
        if "esummary" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "uids": ["555"],
                        "555": {
                            "uid": "555",
                            "title": "A Totally Unrelated Paper",
                            "pubdate": "1999",
                        },
                    },
                },
            )
        return httpx.Response(200, text="<PubmedArticleSet/>")

    pm2 = PubMedProvider()
    pm2.client = httpx.Client(transport=httpx.MockTransport(mismatch_handler))
    assert_that(
        pm2.search_and_enrich(
            _media("Sleep and memory consolidation", MediaType.STUDY),
        ),
    ).is_none()
    pm.close()
    pm2.close()

    ss = SemanticScholarProvider()
    _swap_client(ss, lambda request: httpx.Response(200, json={}))
    assert_that(ss.search_and_enrich(movie)).is_none()
    ss.close()


def test_pubmed_doi_search_canonicalizes_prefixed_doi() -> None:
    """A prefixed DOI is canonicalized before the [doi] search term."""

    seen_terms: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_terms.append(request.url.params.get("term", ""))
        return httpx.Response(200, json={"esearchresult": {"idlist": []}})

    provider = PubMedProvider()
    provider.rate_limiter = _CountingLimiter()
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    media = _media("Sleep study", MediaType.STUDY)
    media.doi = "https://doi.org/10.1000/x"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()
    assert_that(seen_terms[0]).is_equal_to("10.1000/x[doi]")


def test_pubmed_waits_before_every_request() -> None:
    """The esearch + esummary flow waits once per HTTP request."""

    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request.url.path)
        if "esearch" in request.url.path:
            return httpx.Response(
                200,
                json={"esearchresult": {"idlist": ["555"]}},
            )
        return httpx.Response(
            200,
            json={
                "result": {
                    "uids": ["555"],
                    "555": {
                        "uid": "555",
                        "title": "A Totally Unrelated Paper",
                        "pubdate": "1999",
                    },
                },
            },
        )

    provider = PubMedProvider()
    limiter = _CountingLimiter()
    provider.rate_limiter = limiter
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = provider.search_and_enrich(_media("Sleep study", MediaType.STUDY))
    provider.close()

    assert_that(result).is_none()
    assert_that(len(requests_seen)).is_equal_to(2)
    assert_that(limiter.waits).is_equal_to(len(requests_seen))


def test_pubmed_structured_abstract_joins_sections() -> None:
    """Structured abstracts keep every AbstractText section."""

    xml = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>4242</PMID>
      <Article>
        <ArticleTitle>Structured abstract study</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Sleep matters.</AbstractText>
          <AbstractText Label="CONCLUSIONS">Rest improves recall.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author>
        </AuthorList>
        <Journal>
          <Title>Journal</Title>
          <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
        </Journal>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""
    provider = PubMedProvider()
    provider.rate_limiter = _CountingLimiter()
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=xml),
        ),
    )
    media = _media("Structured abstract study", MediaType.STUDY)
    media.pubmed_id = "4242"
    result = enrich_result(provider, media)
    description = result.description or ""
    assert_that(description).contains("Sleep matters.")
    assert_that(description).contains("Rest improves recall.")
    assert_that(description).contains("BACKGROUND")
    assert_that(description).contains("CONCLUSIONS")


@pytest.mark.parametrize(
    ("scenario", "expected_message"),
    [
        ("doi", "PubMed DOI search error"),
        ("search", "PubMed search error"),
        ("summary", "PubMed summary error"),
        ("fetch", "PubMed fetch error for 12345"),
    ],
)
def test_pubmed_http_error_logs_redact_api_key(
    scenario: str, expected_message: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Failed upstream requests must not expose their query-string credentials."""
    configure_logging()
    api_key = "fixture-key-not-for-production"
    sent_keys: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Fail the selected operation after any required search setup."""
        sent_keys.append(request.url.params.get("api_key"))
        if scenario == "summary" and "esearch" in request.url.path:
            return httpx.Response(200, json={"esearchresult": {"idlist": ["12345"]}})
        return httpx.Response(503)

    provider = PubMedProvider(api_key=api_key)
    provider.rate_limiter = _CountingLimiter()
    _swap_client(provider, handler)
    media = _media("Sleep study", MediaType.STUDY)
    if scenario == "doi":
        media.doi = "10.1000/example"
    elif scenario == "fetch":
        media.pubmed_id = "12345"

    with caplog.at_level(logging.INFO):
        with provider:
            assert_that(provider.search_and_enrich(media)).is_none()

    assert_that(sent_keys).is_not_empty().contains_only(api_key)
    assert_that(caplog.text).contains("HTTP Request:", expected_message, "HTTP 503")
    assert_that(caplog.text).does_not_contain(api_key, "api_key=")
