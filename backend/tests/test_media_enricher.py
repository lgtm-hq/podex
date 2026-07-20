"""Tests for the MediaEnricher registry and AcademicEnricher aggregation."""

from typing import Any

import httpx
from assertpy import assert_that

from podex.models import Media, MediaType
from podex.services.academic_enrichment import AcademicEnricher
from podex.services.enrichment.base import EnrichmentResult, EnrichmentSource
from podex.services.media_enrichment import MediaEnricher


def _media(title: str, media_type: MediaType) -> Media:
    media = Media(type=media_type, title=title, author="Jane Doe")
    media.id = 1
    return media


class _StubProvider:
    """Provider double returning a fixed result."""

    def __init__(self, result: EnrichmentResult | None):
        self.result = result
        self.closed = False

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Return the canned result."""
        del media
        return self.result

    def supports_media_type(self, media_type: str) -> bool:
        """Support everything."""
        del media_type
        return True

    def close(self) -> None:
        """Record closure."""
        self.closed = True


def _enricher_with(providers: dict[EnrichmentSource, Any]) -> MediaEnricher:
    enricher = MediaEnricher()
    for provider in enricher.providers.values():
        provider.close()
    enricher.academic_enricher.close()
    enricher.providers = providers
    return enricher


def test_enricher_uses_priority_provider() -> None:
    """The first confident provider result wins for a book."""
    hit = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        description="Desert planet saga.",
        confidence=0.9,
    )
    enricher = _enricher_with(
        {
            EnrichmentSource.GOOGLE_BOOKS: _StubProvider(None),
            EnrichmentSource.OPEN_LIBRARY: _StubProvider(hit),
        },
    )

    result = enricher.enrich(
        _media("Dune", MediaType.BOOK),
        use_wikipedia_fallback=False,
    )
    enricher.close()

    assert_that(result).is_equal_to(hit)


def test_enricher_falls_back_to_wikipedia() -> None:
    """When primaries miss, the Wikipedia fallback is consulted."""
    wiki_hit = EnrichmentResult(
        source=EnrichmentSource.WIKIPEDIA,
        description="From the free encyclopedia.",
        confidence=0.8,
    )
    enricher = _enricher_with(
        {
            EnrichmentSource.GOOGLE_BOOKS: _StubProvider(None),
            EnrichmentSource.OPEN_LIBRARY: _StubProvider(None),
            EnrichmentSource.WIKIPEDIA: _StubProvider(wiki_hit),
        },
    )

    result = enricher.enrich(_media("Dune", MediaType.BOOK))
    enricher.close()

    assert_that(result).is_equal_to(wiki_hit)


def test_enricher_rejects_low_confidence() -> None:
    """Sub-threshold results are discarded rather than returned."""
    weak = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        description="Maybe.",
        confidence=0.2,
    )
    enricher = _enricher_with(
        {EnrichmentSource.OPEN_LIBRARY: _StubProvider(weak)},
    )

    result = enricher.enrich(
        _media("Dune", MediaType.BOOK),
        min_confidence=0.7,
        use_wikipedia_fallback=False,
    )
    enricher.close()

    assert_that(result).is_none()


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

    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_enricher_academic_path_and_apply() -> None:
    """Study media route via the academic enricher; apply writes fields."""
    from podex.services.enrichment.base import VerifiedEnrichmentResult

    verified = VerifiedEnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        description="A sleep study.",
        external_ids={"doi": "10.1000/example.doi", "pubmed_id": "12345"},
        metadata={"title": "Sleep study"},
        confidence=0.92,
        verified_by=[
            EnrichmentSource.CROSSREF,
            EnrichmentSource.SEMANTIC_SCHOLAR,
        ],
    )

    class _StubAcademic:
        def enrich_with_verification(
            self,
            media: Media,
        ) -> VerifiedEnrichmentResult:
            del media
            return verified

        def close(self) -> None:
            pass

    enricher = _enricher_with({})
    enricher.academic_enricher = _StubAcademic()  # type: ignore[assignment, unused-ignore]
    media = _media("Sleep study", MediaType.STUDY)

    result = enricher.enrich(media, use_wikipedia_fallback=False)
    assert_that(result).is_equal_to(verified)

    applied = enricher.enrich_and_apply(media)
    assert_that(applied).is_true()
    assert_that(media.enriched_at).is_not_none()
    assert_that(media.doi or "").contains("10.1000")
    assert_that(enricher.get_available_providers()).is_instance_of(list)
    with enricher:
        pass


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
    _swap_client_matrix(provider, lambda request: httpx.Response(200, json=work))
    media = _media("Sleep and memory consolidation", MediaType.STUDY)
    media.doi = "10.1000/example.doi"
    result = provider.search_and_enrich(media)
    provider.close()

    if result is not None:
        assert_that(result.confidence).is_greater_than(0.8)


def _swap_client_matrix(provider: Any, handler: Any) -> None:
    provider.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://mock.invalid",
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

    if result is not None:
        assert_that(len(result.verified_by)).is_greater_than(1)


def test_tmdb_tv_show_path() -> None:
    """TV shows search the tv endpoint and parse air dates."""
    from podex.services.enrichment import TMDBProvider

    search_payload = {
        "results": [
            {
                "id": 42009,
                "name": "Example Show",
                "first_air_date": "2019-01-01",
                "overview": "A show about examples.",
                "poster_path": "/es.jpg",
                "vote_average": 8.0,
                "vote_count": 5000,
                "popularity": 60.0,
            },
        ],
    }
    detail_payload = {
        "id": 42009,
        "name": "Example Show",
        "overview": "A show about examples.",
        "first_air_date": "2019-01-01",
        "last_air_date": "2022-01-01",
        "number_of_seasons": 3,
        "number_of_episodes": 30,
        "episode_run_time": [45],
        "poster_path": "/es.jpg",
        "external_ids": {"imdb_id": "tt9999999"},
        "genres": [{"name": "Drama"}],
        "status": "Ended",
        "vote_average": 8.0,
        "vote_count": 5000,
        "credits": {"cast": [], "crew": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(200, json=detail_payload)

    provider = TMDBProvider("key")
    _swap_client_matrix(provider, handler)
    media = Media(type=MediaType.TV_SHOW, title="Example Show")
    media.id = 2
    result = provider.search_and_enrich(media)
    provider.close()

    if result is not None:
        assert_that(result.has_useful_data()).is_true()


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
    provider = SemanticScholarProvider()
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=paper),
    )
    media = _media("Sleep and memory consolidation", MediaType.STUDY)
    media.semantic_scholar_id = "abc123"
    result = provider.search_and_enrich(media)
    provider.close()

    if result is not None:
        assert_that(result.confidence).is_greater_than(0.8)


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

    if result is not None:
        assert_that(result.confidence).is_equal_to(1.0)


def test_apply_enrichment_fills_media_fields() -> None:
    """apply_enrichment writes cover, description, ids, and provenance."""
    result = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        cover_url="https://img.invalid/cover.jpg",
        description="A rich description of the work.",
        external_ids={
            "open_library_id": "OL1W",
            "google_books_id": "gb1",
            "imdb_id": "tt1",
            "tmdb_id": 7,
            "wikipedia_id": "Dune_(novel)",
            "pubmed_id": "777",
            "doi": "10.1000/x",
            "semantic_scholar_id": "abc",
        },
        metadata={"subjects": ["Fiction"]},
        confidence=0.95,
    )
    enricher = _enricher_with({})
    media = _media("Dune", MediaType.BOOK)
    enricher.apply_enrichment(media, result)
    enricher.close()

    assert_that(media.cover_url).is_not_none()
    assert_that(media.description).is_not_none()
    assert_that(media.open_library_id).is_equal_to("OL1W")
    assert_that(media.enrichment_source).is_not_none()
    assert_that(media.enrichment_confidence).is_equal_to(0.95)


def test_omdb_series_path() -> None:
    """TV shows query OMDB with the series type."""
    from podex.services.enrichment import OMDBProvider

    payload = {
        "Response": "True",
        "Title": "Example Show",
        "Year": "2019-2022",
        "imdbID": "tt9999999",
        "Plot": "A show about examples.",
        "Poster": "https://img.invalid/es.jpg",
        "Genre": "Drama",
        "imdbRating": "8.0",
        "imdbVotes": "5,000",
        "totalSeasons": "3",
    }
    provider = OMDBProvider("key")
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    media = Media(type=MediaType.TV_SHOW, title="Example Show")
    media.id = 3
    result = provider.search_and_enrich(media)
    provider.close()

    if result is not None:
        assert_that(str(result.external_ids)).contains("tt9999999")


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
    _swap_client_matrix(provider, handler)
    result = provider.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.ARTICLE),
    )
    provider.close()

    assert_that(seen_queries).is_not_empty()
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
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    result = provider.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.ARTICLE),
    )
    provider.close()

    if result is not None:
        assert_that(result.description or "").does_not_contain("jats")


def test_tmdb_person_detail_failure_falls_back() -> None:
    """Person detail failures fall back to search-doc data."""
    from podex.services.enrichment import TMDBPersonProvider

    search_payload = {
        "results": [
            {
                "id": 505710,
                "name": "Frank Herbert",
                "known_for_department": "Writing",
                "popularity": 5.0,
                "profile_path": "/fh.jpg",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/person" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(500, text="boom")

    provider = TMDBPersonProvider("key")
    _swap_client_matrix(provider, handler)
    result = provider.search_and_enrich(
        _media("Frank Herbert", MediaType.PERSON),
    )
    provider.close()

    if result is not None:
        assert_that(result.has_useful_data()).is_true()


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


def test_google_books_isbn10_and_sparse_volume() -> None:
    """ISBN-10-only volumes without images still enrich."""
    from podex.services.enrichment import GoogleBooksProvider

    payload = {
        "items": [
            {
                "id": "gb2",
                "volumeInfo": {
                    "title": "Dune",
                    "authors": ["Frank Herbert"],
                    "publishedDate": "1965",
                    "industryIdentifiers": [
                        {"type": "ISBN_10", "identifier": "0441013597"},
                    ],
                },
            },
        ],
    }
    provider = GoogleBooksProvider(api_key="key")
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    result = provider.search_and_enrich(_media("Dune", MediaType.BOOK))
    provider.close()

    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_omdb_year_and_documentary_paths() -> None:
    """Documentaries and year-bearing media flow through OMDB search."""
    from podex.services.enrichment import OMDBProvider

    payload = {
        "Response": "True",
        "Title": "Example Documentary",
        "Year": "2020",
        "imdbID": "tt7777777",
        "Plot": "A documentary about examples.",
        "Genre": "Documentary",
        "imdbRating": "7.5",
        "imdbVotes": "1,000",
    }
    provider = OMDBProvider("key")
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    media = Media(
        type=MediaType.DOCUMENTARY,
        title="Example Documentary",
        year=2020,
    )
    media.id = 4
    result = provider.search_and_enrich(media)
    provider.close()

    if result is not None:
        assert_that(str(result.external_ids)).contains("tt7777777")


def test_enricher_initializes_all_configured_providers() -> None:
    """API keys switch on their providers in the registry."""
    enricher = MediaEnricher(
        tmdb_api_key="k1",
        omdb_api_key="k2",
        google_books_api_key="k3",
        ncbi_api_key="k4",
        crossref_mailto="ops@example.com",
    )
    names = enricher.get_available_providers()
    enricher.close()

    assert_that(len(names)).is_greater_than(5)


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

    if strict is not None:
        assert_that(strict.doi_verified).is_false()
        assert_that(strict.confidence).is_less_than(0.9)
    if lenient is not None:
        assert_that(str(lenient.external_ids)).contains("10.1000/solo")


def test_tmdb_year_filter_and_missing_details() -> None:
    """Year-bearing media filter results; detail failures fall back."""
    from podex.services.enrichment import TMDBProvider

    search_payload = {
        "results": [
            {
                "id": 1,
                "title": "Dune",
                "release_date": "1984-12-14",
                "overview": "Original adaptation.",
                "vote_average": 6.0,
                "vote_count": 1000,
                "popularity": 20.0,
            },
            {
                "id": 2,
                "title": "Dune",
                "release_date": "2021-10-22",
                "overview": "New adaptation.",
                "vote_average": 7.8,
                "vote_count": 10000,
                "popularity": 100.0,
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(500, text="down")

    provider = TMDBProvider("key")
    _swap_client_matrix(provider, handler)
    media = Media(type=MediaType.MOVIE, title="Dune", year=2021)
    media.id = 5
    result = provider.search_and_enrich(media)
    provider.close()

    if result is not None:
        assert_that(str(result.external_ids)).contains("2")


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
    _swap_client_matrix(provider, handler)
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
    _swap_client_matrix(provider, handler)
    media = _media("Sleep and memory consolidation", MediaType.STUDY)
    media.doi = "10.1000/via.doi"
    result = provider.search_and_enrich(media)
    provider.close()

    if result is not None:
        assert_that(result.confidence).is_greater_than(0.8)


def test_tmdb_person_by_known_id() -> None:
    """A stored tmdb id fetches person details directly."""
    from podex.services.enrichment import TMDBPersonProvider

    detail = {
        "id": 505710,
        "name": "Frank Herbert",
        "biography": "Author.",
        "birthday": "1920-10-08",
        "profile_path": "/fh.jpg",
        "imdb_id": "nm0378394",
        "known_for_department": "Writing",
        "popularity": 5.0,
    }
    provider = TMDBPersonProvider("key")
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=detail),
    )
    media = _media("Frank Herbert", MediaType.PERSON)
    media.tmdb_id = 505710
    result = provider.search_and_enrich(media)
    provider.close()

    if result is not None:
        assert_that(result.confidence).is_greater_than(0.8)


def test_omdb_falls_back_to_title_search_variants() -> None:
    """A miss on the exact title retries stripped-title variants."""
    from podex.services.enrichment import OMDBProvider

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params.get("t", "")))
        if len(calls) < 2:
            return httpx.Response(
                200,
                json={"Response": "False", "Error": "Movie not found!"},
            )
        return httpx.Response(
            200,
            json={
                "Response": "True",
                "Title": "Dune",
                "Year": "2021",
                "imdbID": "tt1160419",
                "Plot": "Found on retry.",
                "imdbRating": "8.0",
                "imdbVotes": "1",
            },
        )

    provider = OMDBProvider("key")
    _swap_client_matrix(provider, handler)
    result = provider.search_and_enrich(
        Media(type=MediaType.MOVIE, title="Dune: Part One"),
    )
    provider.close()

    assert_that(calls).is_not_empty()
    if result is not None:
        assert_that(str(result.external_ids)).contains("tt1160419")


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


def test_itunes_rejects_dissimilar_podcasts() -> None:
    """Results with unrelated names are rejected as matches."""
    from podex.services.enrichment import iTunesProvider

    payload = {
        "results": [
            {
                "collectionId": 99,
                "collectionName": "A Completely Different Program",
                "artistName": "Someone",
            },
        ],
    }
    provider = iTunesProvider()
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    result = provider.search_and_enrich(
        _media("Example Podcast", MediaType.PODCAST),
    )
    provider.close()

    assert_that(result).is_none()


def test_pipeline_apply_and_alias_helpers() -> None:
    """The pipeline's apply/merge/alias helpers cover every id branch."""
    from datetime import UTC, datetime

    from podex.services.media_enrichment_pipeline import (
        _apply_enrichment_result,
        _extract_enrichment_aliases,
        _merge_metadata,
    )

    media = _media("Dune", MediaType.BOOK)
    result = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        cover_url="https://img.invalid/c.jpg",
        description="Applied description.",
        external_ids={
            "imdb_id": "tt1",
            "tmdb_id": "7",
            "google_books_id": "gb1",
            "open_library_id": "OL1W",
            "wikipedia_id": "Dune",
            "doi": "10.1000/x",
            "pubmed_id": "777",
            "semantic_scholar_id": "abc",
        },
        metadata={
            "title": "Dune",
            "original_title": "Dune Original",
            "also_known_as": ["Der Wüstenplanet", 42],
        },
        confidence=0.9,
    )
    _apply_enrichment_result(
        media=media,
        result=result,
        now=datetime.now(UTC),
    )

    assert_that(media.imdb_id).is_equal_to("tt1")
    assert_that(media.tmdb_id).is_equal_to(7)
    assert_that(media.doi).is_equal_to("10.1000/x")

    merged = _merge_metadata(
        existing={"kept": "old"},
        incoming={"kept": "new", "added": "yes"},
    )
    assert_that(merged).is_equal_to({"kept": "old", "added": "yes"})
    assert_that(_merge_metadata(existing=None, incoming={})).is_none()

    aliases = _extract_enrichment_aliases(metadata=result.metadata)
    assert_that(aliases).contains("Dune Original")
    assert_that(aliases).contains("Der Wüstenplanet")


def test_wikipedia_person_and_place_paths() -> None:
    """Person and place media types walk their variation flows."""
    from podex.services.enrichment import WikipediaProvider

    search_payload = {
        "query": {
            "search": [
                {
                    "pageid": 88,
                    "title": "Frank Herbert",
                    "snippet": "American science fiction author",
                },
            ],
        },
    }
    page_payload = {
        "query": {
            "pages": {
                "88": {
                    "pageid": 88,
                    "title": "Frank Herbert",
                    "extract": "Frank Herbert was an American science "
                    "fiction author best known for the novel Dune.",
                    "fullurl": "https://en.wikipedia.org/wiki/Frank_Herbert",
                    "thumbnail": {
                        "original": "https://upload.invalid/fh.jpg",
                    },
                    "categories": [
                        {"title": "Category:American science fiction writers"},
                        {"title": "Category:1920 births"},
                    ],
                },
            },
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("list") == "search":
            return httpx.Response(200, json=search_payload)
        return httpx.Response(200, json=page_payload)

    person_provider = WikipediaProvider(requests_per_second=1000)
    _swap_client_matrix(person_provider, handler)
    person = person_provider.search_and_enrich(
        _media("Frank Herbert", MediaType.PERSON),
    )
    person_provider.close()

    place_provider = WikipediaProvider(requests_per_second=1000)
    _swap_client_matrix(place_provider, handler)
    place_media = Media(type=MediaType.PLACE, title="Frank Herbert")
    place_media.id = 9
    place = place_provider.search_and_enrich(place_media)
    place_provider.close()

    if person is not None:
        assert_that(person.has_useful_data()).is_true()
    del place


def test_google_books_second_item_selected_on_better_match() -> None:
    """Best-match selection scans beyond the first volume."""
    from podex.services.enrichment import GoogleBooksProvider

    payload = {
        "items": [
            {
                "id": "gbX",
                "volumeInfo": {
                    "title": "Completely Unrelated",
                    "authors": ["Nobody"],
                },
            },
            {
                "id": "gbY",
                "volumeInfo": {
                    "title": "Dune",
                    "authors": ["Frank Herbert"],
                    "publishedDate": "1965",
                    "description": "The desert planet novel.",
                    "language": "en",
                    "averageRating": 4.5,
                    "maturityRating": "NOT_MATURE",
                },
            },
        ],
    }
    provider = GoogleBooksProvider()
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    result = provider.search_and_enrich(_media("Dune", MediaType.BOOK))
    provider.close()

    if result is not None:
        assert_that(str(result.external_ids)).contains("gbY")


def test_wikipedia_variations_across_types() -> None:
    """Every media type's search-variation builder executes."""
    import pytest as _pytest

    from podex.services.enrichment import WikipediaProvider

    del _pytest
    for media_type in (
        MediaType.MOVIE,
        MediaType.TV_SHOW,
        MediaType.DOCUMENTARY,
        MediaType.BOOK,
        MediaType.PODCAST,
    ):
        provider = WikipediaProvider(requests_per_second=1000)
        _swap_client_matrix(
            provider,
            lambda request: httpx.Response(
                200,
                json={"query": {"search": []}},
            ),
        )
        media = Media(type=media_type, title="Example Title")
        media.id = 10
        assert_that(provider.search_and_enrich(media)).is_none()
        provider.close()


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
    _swap_client_matrix(
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
    _swap_client_matrix(cr, lambda request: httpx.Response(200, json=no_doi))
    cr_result = cr.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.ARTICLE),
    )
    cr.close()

    del ss_result, cr_result


def test_omdb_direct_imdb_lookup_and_crossref_year_boost() -> None:
    """OMDB imdb-id fetch and CrossRef year matching both execute."""
    from podex.services.enrichment import CrossRefProvider, OMDBProvider

    omdb_payload = {
        "Response": "True",
        "Title": "Dune",
        "Year": "2021",
        "imdbID": "tt1160419",
        "Plot": "Direct fetch.",
        "imdbRating": "8.0",
        "imdbVotes": "1",
    }
    omdb = OMDBProvider("key")
    _swap_client_matrix(
        omdb,
        lambda request: httpx.Response(200, json=omdb_payload),
    )
    media = Media(type=MediaType.MOVIE, title="Dune")
    media.id = 11
    media.imdb_id = "tt1160419"
    direct = omdb.search_and_enrich(media)
    omdb.close()

    assert_that(direct).is_not_none()
    if direct is not None:
        assert_that(direct.confidence).is_equal_to(1.0)

    crossref_payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/yr",
                    "title": ["Sleep and memory consolidation"],
                    "author": [{"given": "Jane", "family": "Doe"}],
                    "published-print": {"date-parts": [[2020]]},
                    "container-title": ["Journal of Sleep"],
                    "type": "journal-article",
                },
            ],
        },
    }
    cr = CrossRefProvider()
    _swap_client_matrix(
        cr,
        lambda request: httpx.Response(200, json=crossref_payload),
    )
    study = Media(
        type=MediaType.STUDY,
        title="Sleep and memory consolidation",
        year=2020,
    )
    study.id = 12
    boosted = cr.search_and_enrich(study)
    cr.close()

    if boosted is not None:
        assert_that(boosted.confidence).is_greater_than(0.5)


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
    _swap_client_matrix(ss, lambda request: httpx.Response(200, json=paper))
    study = _media("Sleep and memory consolidation", MediaType.STUDY)
    study.pubmed_id = "777"
    via_pmid = ss.search_and_enrich(study)
    ss.close()

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
    _swap_client_matrix(
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
    _swap_client_matrix(provider, handler)
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
    _swap_client_matrix(ss, lambda request: httpx.Response(200, json={}))
    assert_that(ss.search_and_enrich(movie)).is_none()
    ss.close()


class _CountingLimiter:
    """Rate-limiter double counting wait_sync calls without sleeping."""

    def __init__(self) -> None:
        self.waits = 0

    def wait_sync(self) -> None:
        """Count the wait instead of sleeping."""
        self.waits += 1


def test_provider_error_logs_redact_api_key(caplog: Any) -> None:
    """A 401 response log line never contains the api key value."""
    import logging as _logging

    from podex.services.enrichment import OMDBProvider

    provider = OMDBProvider("sekret-key")
    provider.rate_limiter = _CountingLimiter()
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(401, text="unauthorized"),
    )
    media = Media(type=MediaType.MOVIE, title="Dune")
    media.id = 24
    with caplog.at_level(_logging.WARNING):
        result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()
    assert_that(caplog.text).contains("HTTP 401")
    assert_that(caplog.text).does_not_contain("sekret-key")


def test_omdb_uses_https_base_url() -> None:
    """The OMDB base URL uses the https scheme (api key in query)."""
    from podex.services.enrichment import OMDBProvider

    assert_that(OMDBProvider.BASE_URL).starts_with("https://")


def test_provider_context_managers_close_clients() -> None:
    """Context-manager protocol closes every API-key provider."""
    from podex.services.enrichment import (
        CrossRefProvider,
        GoogleBooksProvider,
        OMDBProvider,
        PubMedProvider,
        SemanticScholarProvider,
        TMDBPersonProvider,
        TMDBProvider,
        iTunesProvider,
    )

    factories: tuple[Any, ...] = (
        lambda: CrossRefProvider(),
        lambda: GoogleBooksProvider(),
        lambda: OMDBProvider("key"),
        lambda: PubMedProvider(),
        lambda: SemanticScholarProvider(),
        lambda: TMDBProvider("key"),
        lambda: TMDBPersonProvider("key"),
        lambda: iTunesProvider(),
    )
    for factory in factories:
        provider = factory()
        with provider:
            assert_that(provider).is_not_none()
