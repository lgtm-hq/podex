"""Tests for academic enrichment providers and aggregation."""

import httpx
from assertpy import assert_that

from podex.models import Media, MediaType
from podex.services.academic_enrichment import AcademicEnricher
from podex.services.enrichment.base import EnrichmentResult, EnrichmentSource
from tests.enrichment.conftest import (
    _academic_with,
    _CountingLimiter,
    _media,
    _RaisingProvider,
    _SlowProvider,
    _StubProvider,
    _swap_client,
)


def test_academic_enricher_degrades_gracefully() -> None:
    """With no upstream hits, academic verification yields None."""
    enricher = AcademicEnricher()
    for provider in enricher.providers.values():
        provider.client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={}),
            ),
            base_url="https://mock.invalid",
        )

    result = enricher.enrich_with_verification(
        _media("Unfindable study", MediaType.STUDY),
    )
    enricher.close()

    assert_that(result).is_none()


def test_academic_enricher_doi_normalization() -> None:
    """DOI and title normalization behave as documented."""
    enricher = AcademicEnricher()
    assert_that(
        enricher._normalize_doi("https://doi.org/10.1000/Example.DOI"),
    ).is_equal_to("10.1000/example.doi")
    assert_that(
        enricher._normalize_title("The Study: A Review!"),
    ).does_not_contain(":")
    assert_that(enricher.supports_media_type("study")).is_true()
    assert_that(enricher.supports_media_type("movie")).is_false()
    enricher.close()


def test_academic_enricher_cross_validates_by_doi() -> None:
    """Two sources agreeing on a DOI produce a verified result."""
    doi = "10.1000/example.doi"
    agree_a = EnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        description="A sleep study.",
        external_ids={"doi": doi},
        metadata={"title": "Sleep study"},
        confidence=0.9,
    )
    agree_b = EnrichmentResult(
        source=EnrichmentSource.SEMANTIC_SCHOLAR,
        description="A sleep study.",
        external_ids={"doi": doi},
        metadata={"title": "Sleep study"},
        confidence=0.85,
    )
    enricher = AcademicEnricher()
    for provider in enricher.providers.values():
        provider.close()
    enricher.providers = {
        EnrichmentSource.CROSSREF: _StubProvider(agree_a),
        EnrichmentSource.SEMANTIC_SCHOLAR: _StubProvider(agree_b),
    }

    result = enricher.enrich_with_verification(
        _media("Sleep study", MediaType.STUDY),
    )

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(len(result.verified_by)).is_greater_than(1)
        assert_that(result.confidence).is_greater_than(0.8)
        assert_that(result.doi_verified).is_true()


def test_pubmed_happy_path_via_esearch_and_efetch() -> None:
    """PubMed search ids resolve through the XML fetch into a result."""
    from podex.services.enrichment import PubMedProvider

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
    result = provider.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.STUDY),
    )
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()
        assert_that(result.metadata["title"]).is_equal_to(
            "Sleep and memory consolidation",
        )


def test_crossref_direct_doi_lookup() -> None:
    """A known DOI fetches the work directly."""
    work = {
        "message": {
            "DOI": "10.1000/example.doi",
            "title": ["Sleep and memory consolidation"],
            "author": [{"given": "Jane", "family": "Doe"}],
            "issued": {"date-parts": [[2020, 1, 1]]},
            "container-title": ["Journal of Sleep"],
            "URL": "https://doi.invalid/example",
            "abstract": "A study of sleep.",
            "is-referenced-by-count": 42,
            "type": "journal-article",
            "publisher": "Example Press",
        },
    }
    from podex.services.enrichment import CrossRefProvider

    provider = CrossRefProvider()
    _swap_client(provider, lambda request: httpx.Response(200, json=work))
    media = _media("Sleep and memory consolidation", MediaType.STUDY)
    media.doi = "10.1000/example.doi"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.confidence).is_greater_than(0.8)
        assert_that(result.metadata["title"]).is_equal_to(
            "Sleep and memory consolidation",
        )


def test_academic_verifies_by_title_without_dois() -> None:
    """Sources agreeing on title (no DOIs) still verify."""
    a = EnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        description="A sleep study.",
        metadata={"title": "Sleep study"},
        confidence=0.8,
    )
    b = EnrichmentResult(
        source=EnrichmentSource.SEMANTIC_SCHOLAR,
        description="A sleep study.",
        metadata={"title": "Sleep Study"},
        confidence=0.75,
    )
    enricher = AcademicEnricher()
    for provider in enricher.providers.values():
        provider.close()
    enricher.providers = {
        EnrichmentSource.CROSSREF: _StubProvider(a),
        EnrichmentSource.SEMANTIC_SCHOLAR: _StubProvider(b),
    }

    result = enricher.enrich_with_verification(
        _media("Sleep study", MediaType.STUDY),
    )

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(len(result.verified_by)).is_greater_than(1)


def test_semantic_scholar_direct_paper_id() -> None:
    """A stored semantic_scholar_id fetches the paper directly."""
    from podex.services.enrichment import SemanticScholarProvider

    paper = {
        "paperId": "abc123",
        "title": "Sleep and memory consolidation",
        "abstract": "A study.",
        "year": 2020,
        "citationCount": 100,
        "authors": [{"name": "Jane Doe"}],
        "externalIds": {"DOI": "10.1000/example.doi"},
        "url": "https://ss.invalid/abc123",
        "venue": "Journal of Sleep",
    }
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(200, json=paper)

    provider = SemanticScholarProvider()
    _swap_client(provider, handler)
    media = _media("Sleep and memory consolidation", MediaType.STUDY)
    media.semantic_scholar_id = "abc123"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(seen_paths).is_equal_to(["/paper/abc123"])
    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.confidence).is_equal_to(1.0)
        assert_that(result.metadata["title"]).is_equal_to(
            "Sleep and memory consolidation",
        )


def test_pubmed_direct_pmid_fetch() -> None:
    """A stored pubmed_id skips search and fetches the article."""
    from podex.services.enrichment import PubMedProvider

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
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.confidence).is_equal_to(1.0)
        assert_that(result.metadata["title"]).is_equal_to("Direct fetch study")


def test_crossref_search_with_author() -> None:
    """Author names flow into CrossRef search queries."""
    from podex.services.enrichment import CrossRefProvider

    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/a",
                    "title": ["Sleep and memory consolidation"],
                    "author": [{"given": "Jane", "family": "Doe"}],
                    "issued": {"date-parts": [[2020]]},
                    "container-title": ["Journal of Sleep"],
                    "type": "journal-article",
                },
            ],
        },
    }
    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_queries.append(str(request.url))
        return httpx.Response(200, json=payload)

    provider = CrossRefProvider(mailto="ops@example.com")
    _swap_client(provider, handler)
    result = provider.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.ARTICLE),
    )
    provider.close()

    assert_that(seen_queries).is_not_empty()
    assert_that(
        [q for q in seen_queries if "query.author=Doe" in q],
    ).is_not_empty()
    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_pubmed_doi_search_path() -> None:
    """A media DOI resolves through the PubMed DOI search to an article."""
    from podex.services.enrichment import PubMedProvider

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
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_crossref_rich_work_parsing() -> None:
    """Print dates, subjects, and JATS abstracts parse into metadata."""
    from podex.services.enrichment import CrossRefProvider

    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/rich",
                    "title": ["Sleep and memory consolidation"],
                    "author": [
                        {"given": "Jane", "family": "Doe"},
                        {"given": "John", "family": "Smith"},
                    ],
                    "published-print": {"date-parts": [[2020, 6, 15]]},
                    "container-title": ["Journal of Sleep"],
                    "URL": "https://doi.invalid/rich",
                    "abstract": "<jats:p>A study of sleep.</jats:p>",
                    "is-referenced-by-count": 42,
                    "type": "journal-article",
                    "publisher": "Example Press",
                    "subject": ["Neuroscience"],
                    "ISSN": ["1234-5678"],
                    "volume": "12",
                    "issue": "3",
                    "page": "100-110",
                },
            ],
        },
    }
    provider = CrossRefProvider()
    _swap_client(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    result = provider.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.ARTICLE),
    )
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.description or "").does_not_contain("jats")


def test_pubmed_rich_article_parse() -> None:
    """Mesh terms, PMC ids, months, and DOIs parse from article XML."""
    from podex.services.enrichment import PubMedProvider

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
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(str(result.metadata)).contains("Sleep")


def test_academic_single_source_with_doi_verification() -> None:
    """min_sources=1 accepts a single DOI-bearing source."""
    only = EnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        description="Solo study.",
        external_ids={"doi": "10.1000/solo"},
        metadata={"title": "Solo study"},
        confidence=0.9,
    )
    enricher = AcademicEnricher()
    for provider in enricher.providers.values():
        provider.close()
    enricher.providers = {EnrichmentSource.CROSSREF: _StubProvider(only)}

    strict = enricher.enrich_with_verification(
        _media("Solo study", MediaType.STUDY),
        min_sources=2,
    )
    lenient = enricher.enrich_with_verification(
        _media("Solo study", MediaType.STUDY),
        min_sources=1,
    )

    assert_that(strict).is_not_none()
    if strict is not None:
        assert_that(strict.doi_verified).is_false()
        assert_that(strict.confidence).is_less_than(0.9)
    assert_that(lenient).is_not_none()
    if lenient is not None:
        assert_that(str(lenient.external_ids)).contains("10.1000/solo")


def test_crossref_doi_prefix_normalization_and_404() -> None:
    """Prefixed DOIs normalize; 404 falls back to search gracefully."""
    from podex.services.enrichment import CrossRefProvider

    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.startswith("/works/10.1000"):
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"message": {"items": []}})

    provider = CrossRefProvider()
    _swap_client(provider, handler)
    media = _media("Missing study", MediaType.STUDY)
    media.doi = "https://doi.org/10.1000/Missing"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()
    assert_that(seen_paths[0]).contains("/works/10.1000/missing")


def test_semantic_scholar_doi_lookup_and_search_fallback() -> None:
    """A media DOI tries the paper endpoint, then falls back to search."""
    from podex.services.enrichment import SemanticScholarProvider

    paper = {
        "paperId": "viaDoi",
        "title": "Sleep and memory consolidation",
        "abstract": "Via DOI.",
        "year": 2020,
        "citationCount": 10,
        "authors": [{"name": "Jane Doe"}],
        "externalIds": {"DOI": "10.1000/via.doi"},
        "url": "https://ss.invalid/viaDoi",
        "venue": "Journal",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/paper/DOI:"):
            return httpx.Response(200, json=paper)
        return httpx.Response(200, json={"data": []})

    provider = SemanticScholarProvider(api_key="key")
    _swap_client(provider, handler)
    media = _media("Sleep and memory consolidation", MediaType.STUDY)
    media.doi = "10.1000/via.doi"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.confidence).is_greater_than(0.8)


def test_pubmed_author_scoped_search() -> None:
    """Author names scope the PubMed query with the [Author] field."""
    from podex.services.enrichment import PubMedProvider

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


def test_semantic_scholar_minimal_paper_and_crossref_no_doi() -> None:
    """Sparse payloads parse without optional fields."""
    from podex.services.enrichment import (
        CrossRefProvider,
        SemanticScholarProvider,
    )

    sparse_paper = {
        "data": [
            {
                "paperId": "sparse1",
                "title": "Sleep and memory consolidation",
            },
        ],
    }
    ss = SemanticScholarProvider()
    _swap_client(
        ss,
        lambda request: httpx.Response(200, json=sparse_paper),
    )
    ss_result = ss.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.STUDY),
    )
    ss.close()

    no_doi = {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/minimal",
                    "title": ["Sleep and memory consolidation"],
                    "type": "journal-article",
                },
            ],
        },
    }
    cr = CrossRefProvider()
    _swap_client(cr, lambda request: httpx.Response(200, json=no_doi))
    cr_result = cr.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.ARTICLE),
    )
    cr.close()

    del ss_result, cr_result


def test_semantic_scholar_pmid_route_and_omdb_weak_match() -> None:
    """PMID-based lookup and OMDB weak-match rejection both execute."""
    from podex.services.enrichment import OMDBProvider, SemanticScholarProvider

    paper = {
        "paperId": "viaPmid",
        "title": "Sleep and memory consolidation",
        "abstract": "Via PMID.",
        "year": 2020,
        "authors": [{"name": "Jane Doe"}],
        "externalIds": {"DOI": "10.1000/pmid.doi"},
    }
    ss = SemanticScholarProvider()
    _swap_client(ss, lambda request: httpx.Response(200, json=paper))
    study = _media("Sleep and memory consolidation", MediaType.STUDY)
    study.pubmed_id = "777"
    via_pmid = ss.search_and_enrich(study)
    ss.close()

    assert_that(via_pmid).is_not_none()
    if via_pmid is not None:
        assert_that(via_pmid.confidence).is_greater_than(0.9)

    weak_payload = {
        "Response": "True",
        "Title": "A Wholly Different Film",
        "Year": "1999",
        "imdbID": "tt0000001",
        "Plot": "Unrelated.",
        "imdbRating": "5.0",
        "imdbVotes": "10",
    }
    omdb = OMDBProvider("key")
    _swap_client(
        omdb,
        lambda request: httpx.Response(200, json=weak_payload),
    )
    weak = omdb.search_and_enrich(
        Media(type=MediaType.MOVIE, title="Dune"),
    )
    omdb.close()

    assert_that(weak).is_none()


def test_crossref_doi_lookup_http_error() -> None:
    """Transport failures on the DOI endpoint degrade to search."""
    from podex.services.enrichment import CrossRefProvider

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/works/"):
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"message": {"items": []}})

    provider = CrossRefProvider()
    _swap_client(provider, handler)
    media = _media("Sleep study", MediaType.STUDY)
    media.doi = "10.1000/error.doi"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()


def test_pubmed_and_semantic_scholar_reject_paths() -> None:
    """Unsupported types and title mismatches yield None."""
    from podex.services.enrichment import PubMedProvider, SemanticScholarProvider

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
    from podex.services.enrichment import PubMedProvider

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


def test_semantic_scholar_canonicalizes_prefixed_doi() -> None:
    """A prefixed DOI is canonicalized before the DOI: paper lookup."""
    from podex.services.enrichment import SemanticScholarProvider

    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.startswith("/paper/DOI:"):
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"data": []})

    provider = SemanticScholarProvider()
    provider.rate_limiter = _CountingLimiter()
    _swap_client(provider, handler)
    media = _media("Sleep study", MediaType.STUDY)
    media.doi = "doi:10.1000/x"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()
    assert_that(seen_paths[0]).is_equal_to("/paper/DOI:10.1000/x")


def test_pubmed_waits_before_every_request() -> None:
    """The esearch + esummary flow waits once per HTTP request."""
    from podex.services.enrichment import PubMedProvider

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
    from podex.services.enrichment import PubMedProvider

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
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        description = result.description or ""
        assert_that(description).contains("Sleep matters.")
        assert_that(description).contains("Rest improves recall.")
        assert_that(description).contains("BACKGROUND")
        assert_that(description).contains("CONCLUSIONS")


def test_academic_fanout_isolates_provider_failures() -> None:
    """A raising provider does not prevent other results from aggregating."""
    good = EnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        description="A sleep study.",
        external_ids={"doi": "10.1000/example.doi"},
        metadata={"title": "Sleep study"},
        confidence=0.9,
    )
    enricher = _academic_with(
        {
            EnrichmentSource.CROSSREF: _StubProvider(good),
            EnrichmentSource.PUBMED: _RaisingProvider(),
        },
    )

    result = enricher.enrich_with_verification(
        _media("Sleep study", MediaType.STUDY),
    )

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.verified_by).is_equal_to([EnrichmentSource.CROSSREF])


def test_academic_fanout_drops_slow_provider() -> None:
    """A provider sleeping past the aggregate deadline is dropped."""
    fast = EnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        description="A sleep study.",
        external_ids={"doi": "10.1000/example.doi"},
        metadata={"title": "Sleep study"},
        confidence=0.9,
    )
    slow = EnrichmentResult(
        source=EnrichmentSource.SEMANTIC_SCHOLAR,
        description="A sleep study.",
        external_ids={"doi": "10.1000/example.doi"},
        metadata={"title": "Sleep study"},
        confidence=0.95,
    )
    enricher = _academic_with(
        {
            EnrichmentSource.CROSSREF: _StubProvider(fast),
            EnrichmentSource.SEMANTIC_SCHOLAR: _SlowProvider(slow, delay=1.0),
        },
        aggregate_timeout_seconds=0.1,
    )

    result = enricher.enrich_with_verification(
        _media("Sleep study", MediaType.STUDY),
    )

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.verified_by).is_equal_to([EnrichmentSource.CROSSREF])


def test_academic_title_verification_requires_explicit_titles() -> None:
    """Results lacking provider titles never verify by assumption."""
    a = EnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        description="A sleep study. With details.",
        confidence=0.8,
    )
    b = EnrichmentResult(
        source=EnrichmentSource.SEMANTIC_SCHOLAR,
        description="A sleep study. With more details.",
        confidence=0.75,
    )
    enricher = _academic_with(
        {
            EnrichmentSource.CROSSREF: _StubProvider(a),
            EnrichmentSource.SEMANTIC_SCHOLAR: _StubProvider(b),
        },
    )

    verified = enricher._verify_by_title(
        {
            EnrichmentSource.CROSSREF: a,
            EnrichmentSource.SEMANTIC_SCHOLAR: b,
        },
    )
    assert_that(verified).is_empty()

    result = enricher.enrich_with_verification(
        _media("Sleep study", MediaType.STUDY),
    )
    assert_that(result).is_not_none()
    if result is not None:
        assert_that(len(result.verified_by)).is_equal_to(1)


def test_academic_title_verification_needs_two_title_bearers() -> None:
    """A single title-bearing result cannot title-verify a pair."""
    titled = EnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        metadata={"title": "Sleep study"},
        confidence=0.8,
    )
    untitled = EnrichmentResult(
        source=EnrichmentSource.SEMANTIC_SCHOLAR,
        description="A sleep study.",
        confidence=0.75,
    )
    enricher = _academic_with({})

    verified = enricher._verify_by_title(
        {
            EnrichmentSource.CROSSREF: titled,
            EnrichmentSource.SEMANTIC_SCHOLAR: untitled,
        },
    )

    assert_that(verified).is_empty()
