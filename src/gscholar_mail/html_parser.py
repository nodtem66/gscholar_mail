import re

from bs4 import BeautifulSoup

from gscholar_mail.paper_record import PaperRecord

CLASS_TITLE_FILTER = re.compile(r"gse_alrt_title$")
CLEAN_TEXT_FILTER = re.compile(r"( +)|[\n\xa0]+|(?:\\[xX]\w\w|\\[uU]\w)+")


def _clean_text(st: str | None) -> str:
    """Clean Scholar text safely, including empty or missing elements."""
    if not st:
        return ""
    if st.startswith("[") and "]" in st:
        st = st[st.find("]") + 1 :]
    st = CLEAN_TEXT_FILTER.sub(lambda m: " " if m.group(1) else "", st)
    return st.strip()


class HTMLParser:
    email_title: str
    papers: list[PaperRecord]

    def __init__(self, email_title: str):
        self.email_title = email_title
        self.papers = []

    def parse(self, msg: str):
        """Parse only complete Scholar entries; incomplete entries are skipped."""
        if not msg:
            return
        # The paper title is in <a> tag with class "gse_alrt_title"
        # <h3>
        #  <a class="gse_alrt_title" href="https://scholar.google.com/scholar?cluster=1234567890">Paper Title</a>
        # </h3>
        # <div>Author1, Author2, Author3</div>
        # <div>Data</div>
        soup = BeautifulSoup(msg, "lxml")
        links = soup.find_all("a", class_=CLASS_TITLE_FILTER)
        for link in links:
            try:
                link_parent = link.parent
                if link_parent is None:
                    continue
                if link_parent.name != "h3":
                    continue
                siblings = link_parent.find_next_siblings(name="div", limit=2)
                if len(siblings) < 2:
                    continue
                author, data_element = siblings[0], siblings[1]
                if len(data_element.text) == 0 or len(author.text) == 0:
                    # Should we accept papers with either empty data or authors? For now, we skip them.
                    continue

                paper = PaperRecord(self.email_title)
                paper.title = _clean_text(link.text)
                paper.idx = self._create_idx_from_title(paper.title)
                paper.data = _clean_text(data_element.text)
                paper.authors = _clean_text(author.text)
                paper.year = self._parse_year(author.text)
                paper.link = str(link["href"])
                self.papers.append(paper)
            except AttributeError:
                pass

    def _parse_year(self, text: str):
        try:
            year = int(text.replace(" ", "").split(",")[-1])
        except ValueError:
            year = None
        return year

    def _create_idx_from_title(self, title: str):
        # Preserve the legacy sheet-compatible normalized-title key.
        return re.sub(r"[:;]", " ", title.strip(". ").upper())

    def check_valid_papers(self) -> bool:
        for paper in self.papers:
            if paper.title == "" or paper.authors == "":
                return False
        return True

    def __str__(self):
        return str(self.papers)

    def __repr__(self) -> str:
        return self.__str__()
