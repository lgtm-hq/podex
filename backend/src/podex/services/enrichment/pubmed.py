"""PubMed/NCBI E-utilities enrichment provider."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET  # nosec B405 - PubMed API responses
from typing import TYPE_CHECKING, Any

import httpx

from podex.models.media import MediaType
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
    canonicalize_doi,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class PubMedProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich academic papers from PubMed/NCBI databases.

    Uses NCBI E-utilities API (free, 3 req/sec without API key, 10/sec with key).

    Args:
        api_key: Optional NCBI API key for higher rate limits.
        requests_per_second: Rate limit for API calls.
    """

    SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    source = EnrichmentSource.PUBMED
    SUPPORTED_TYPES = {MediaType.STUDY, MediaType.ARTICLE}

    def __init__(
        self,
        api_key: str | None = None,
        requests_per_second: float = 2.5,
    ) -> None:
        super().__init__(requests_per_second)
        self.api_key = api_key
        self.client = httpx.Client(timeout=30.0)

    def supports_media_type(self, media_type: str | MediaType) -> bool:
        """Check if PubMed supports this media type."""
        try:
            return MediaType(media_type) in self.SUPPORTED_TYPES
        except ValueError:
            return False

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search PubMed and enrich media item.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        # 1. Direct lookup by PMID if available
        if media.pubmed_id:
            self.rate_limiter.wait_sync()
            article = self._fetch_article(media.pubmed_id)
            if article:
                return self._build_result(article, confidence=1.0)

        # 2. Direct lookup by DOI if available
        if media.doi:
            self.rate_limiter.wait_sync()
            pmid = self._search_by_doi(media.doi)
            if pmid:
                self.rate_limiter.wait_sync()
                article = self._fetch_article(pmid)
                if article:
                    return self._build_result(article, confidence=0.95)

        # 3. Search by title + author
        self.rate_limiter.wait_sync()
        results = self._search(media.title, media.author)

        if not results:
            logger.debug(f"No PubMed results for: {media.title}")
            return None

        # Find best match
        best_match = self._find_best_match(results, media)
        if not best_match:
            return None

        pmid, confidence = best_match
        self.rate_limiter.wait_sync()
        article = self._fetch_article(pmid)
        if article:
            return self._build_result(article, confidence)

        return None

    def _search_by_doi(self, doi: str) -> str | None:
        """Search PubMed by DOI.

        Args:
            doi: DOI to search for.

        Returns:
            PMID if found, None otherwise.
        """
        params: dict[str, str | int] = {
            "db": "pubmed",
            "term": f"{canonicalize_doi(doi)}[doi]",
            "retmode": "json",
            "retmax": 1,
        }
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            response = self.client.get(self.SEARCH_URL, params=params)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            id_list: list[str] = data.get("esearchresult", {}).get("idlist", [])
            if id_list:
                return id_list[0]
        except httpx.HTTPError as e:
            logger.warning(f"PubMed DOI search error: {e}")

        return None

    def _search(self, title: str, author: str | None) -> list[dict[str, Any]]:
        """Search PubMed by title and author.

        Args:
            title: Paper title.
            author: Optional author name.

        Returns:
            List of search results with PMID and title.
        """
        # Build search query
        query_parts = [f"{title}[Title]"]
        if author:
            # Take first author's last name
            first_author = author.split(",")[0].split(" and ")[0].strip()
            last_name = first_author.split()[-1] if first_author else None
            if last_name:
                query_parts.append(f"{last_name}[Author]")

        query = " AND ".join(query_parts)

        params: dict[str, str | int] = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": 10,
        }
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            response = self.client.get(self.SEARCH_URL, params=params)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            id_list: list[str] = data.get("esearchresult", {}).get("idlist", [])

            if not id_list:
                return []

            # Get summaries for found PMIDs
            return self._get_summaries(id_list)
        except httpx.HTTPError as e:
            logger.warning(f"PubMed search error: {e}")
            return []

    def _get_summaries(self, pmids: list[str]) -> list[dict[str, Any]]:
        """Get article summaries for given PMIDs.

        Args:
            pmids: List of PubMed IDs.

        Returns:
            List of article summaries.
        """
        params: dict[str, str] = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json",
        }
        if self.api_key:
            params["api_key"] = self.api_key

        self.rate_limiter.wait_sync()
        try:
            response = self.client.get(self.SUMMARY_URL, params=params)
            response.raise_for_status()
            data = response.json()

            results = []
            result_data = data.get("result", {})
            for pmid in pmids:
                if pmid in result_data:
                    article = result_data[pmid]
                    results.append(
                        {
                            "pmid": pmid,
                            "title": article.get("title", ""),
                            "authors": [
                                a.get("name", "") for a in article.get("authors", [])
                            ],
                            "source": article.get("source", ""),
                            "pubdate": article.get("pubdate", ""),
                        }
                    )
            return results
        except httpx.HTTPError as e:
            logger.warning(f"PubMed summary error: {e}")
            return []

    def _fetch_article(self, pmid: str) -> dict[str, Any] | None:
        """Fetch full article details by PMID.

        Args:
            pmid: PubMed ID.

        Returns:
            Article data or None.
        """
        params = {
            "db": "pubmed",
            "id": pmid,
            "retmode": "xml",
        }
        if self.api_key:
            params["api_key"] = self.api_key

        try:
            response = self.client.get(self.FETCH_URL, params=params)
            response.raise_for_status()
            return self._parse_pubmed_xml(response.text, pmid)
        except httpx.HTTPError as e:
            logger.warning(f"PubMed fetch error for {pmid}: {e}")
            return None

    def _parse_pubmed_xml(self, xml_text: str, pmid: str) -> dict[str, Any] | None:
        """Parse PubMed XML response.

        Args:
            xml_text: XML response text.
            pmid: PubMed ID.

        Returns:
            Parsed article data or None.
        """
        try:
            root = ET.fromstring(xml_text)  # noqa: S314 # nosec B314
            article = root.find(".//PubmedArticle")
            if article is None:
                return None

            medline = article.find("MedlineCitation")
            if medline is None:
                return None

            article_data = medline.find("Article")
            if article_data is None:
                return None

            # Extract title
            title_elem = article_data.find("ArticleTitle")
            title = title_elem.text if title_elem is not None else ""

            # Extract abstract (structured abstracts have multiple sections)
            abstract_sections: list[str] = []
            for abstract_elem in article_data.findall(".//AbstractText"):
                text = (abstract_elem.text or "").strip()
                if not text:
                    continue
                label = (abstract_elem.get("Label") or "").strip()
                abstract_sections.append(f"{label}: {text}" if label else text)
            abstract = " ".join(abstract_sections)

            # Extract authors
            authors: list[str] = []
            author_list = article_data.find("AuthorList")
            if author_list is not None:
                for author in author_list.findall("Author"):
                    last_name = author.find("LastName")
                    fore_name = author.find("ForeName")
                    if last_name is not None and fore_name is not None:
                        authors.append(f"{fore_name.text} {last_name.text}")
                    elif last_name is not None and last_name.text is not None:
                        authors.append(last_name.text)

            # Extract journal
            journal_elem = article_data.find(".//Journal/Title")
            journal = journal_elem.text if journal_elem is not None else ""

            # Extract DOI
            doi = None
            article_id_list = article.find(".//PubmedData/ArticleIdList")
            if article_id_list is not None:
                for aid in article_id_list.findall("ArticleId"):
                    if aid.get("IdType") == "doi":
                        doi = aid.text
                        break

            # Extract publication date
            pub_date = None
            pub_date_elem = article_data.find(".//PubDate")
            if pub_date_elem is not None:
                year = pub_date_elem.find("Year")
                month = pub_date_elem.find("Month")
                if year is not None:
                    pub_date = year.text
                    if month is not None:
                        pub_date = f"{year.text}-{month.text}"

            # Extract MeSH terms
            mesh_terms = []
            mesh_heading_list = medline.find("MeshHeadingList")
            if mesh_heading_list is not None:
                for heading in mesh_heading_list.findall("MeshHeading"):
                    descriptor = heading.find("DescriptorName")
                    if descriptor is not None:
                        mesh_terms.append(descriptor.text)

            # Extract PMC ID if available
            pmc_id = None
            if article_id_list is not None:
                for aid in article_id_list.findall("ArticleId"):
                    if aid.get("IdType") == "pmc":
                        pmc_id = aid.text
                        break

            return {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal,
                "doi": doi,
                "pub_date": pub_date,
                "mesh_terms": mesh_terms,
                "pmc_id": pmc_id,
            }
        except ET.ParseError as e:
            logger.warning(f"PubMed XML parse error: {e}")
            return None

    def _find_best_match(
        self,
        results: list[dict[str, Any]],
        media: Media,
    ) -> tuple[str, float] | None:
        """Find best matching result.

        Args:
            results: Search results from PubMed.
            media: Original media item.

        Returns:
            Tuple of (pmid, confidence) or None.
        """
        if not results:
            return None

        best_pmid = None
        best_score = 0.0

        for result in results[:5]:
            result_title = result.get("title", "")

            # Calculate title similarity
            title_score = self._calculate_title_similarity(media.title, result_title)

            # Check author match
            author_boost = 0.0
            if media.author:
                result_authors = result.get("authors", [])
                for author in result_authors:
                    author_sim = self._calculate_title_similarity(media.author, author)
                    if author_sim > 0.5:
                        author_boost = 0.15
                        break

            total_score = title_score + author_boost

            if total_score > best_score:
                best_score = total_score
                best_pmid = result.get("pmid")

        if best_pmid and best_score >= 0.6:
            return (best_pmid, min(best_score, 0.9))

        return None

    def _build_result(
        self, article: dict[str, Any], confidence: float
    ) -> EnrichmentResult:
        """Build enrichment result from PubMed article.

        Args:
            article: PubMed article data.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with all available data.
        """
        external_ids: dict[str, str | int] = {
            "pubmed_id": article["pmid"],
        }

        if article.get("doi"):
            external_ids["doi"] = article["doi"]
        if article.get("pmc_id"):
            external_ids["pmc_id"] = article["pmc_id"]

        metadata: dict[str, Any] = {}

        # Title (explicit contract for cross-source title verification)
        if article.get("title"):
            metadata["title"] = article["title"]

        if article.get("authors"):
            metadata["authors"] = article["authors"]
        if article.get("journal"):
            metadata["journal"] = article["journal"]
        if article.get("pub_date"):
            metadata["publication_date"] = article["pub_date"]
        if article.get("mesh_terms"):
            metadata["mesh_terms"] = article["mesh_terms"]

        # Build PubMed Central URL if available
        if article.get("pmc_id"):
            metadata["pmc_url"] = (
                f"https://www.ncbi.nlm.nih.gov/pmc/articles/{article['pmc_id']}"
            )

        return EnrichmentResult(
            source=self.source,
            cover_url=None,  # PubMed doesn't provide cover images
            description=article.get("abstract"),
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> PubMedProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
