import dataclasses
from datetime import datetime

# import pandas as pd
# from typing_extensions import Self


DATA_FOLDER = r"./data"
PAPERS_LABEL = "Subscribe/Gscholar"
PREV_PAPERS_FILE = r"prev_papers.pickle"
ARCHIVE_TSV = r"archive.tsv"


def _get_current_datetime() -> str:
    return datetime.now().isoformat()


@dataclasses.dataclass
class PaperRecord:
    """Class to represent a paper record in the database
    The primary key is idx, which is derived from the title.
    """

    title: str
    data: str
    authors: str
    link: str
    idx: str
    email_title: str
    year: int | None
    status: int
    updated_at: str | None
    created_at: str | None

    def __init__(self, email_title: str):
        self.title = ""
        self.data = ""
        self.authors = ""
        self.link = ""
        self.idx = ""
        self.email_title = email_title
        self.year = None
        self.status = 0
        self.updated_at = _get_current_datetime()
        self.created_at = _get_current_datetime()

    def __eq__(self, paper):
        return self.idx == paper.idx


# class PaperAggregator:
#     def __init__(self):
#         self.paper_list = []

#     def add(self, paper):
#         try:
#             idx = self.paper_list.index(paper)
#         except ValueError:  # Paper not in the list
#             self.paper_list.append(paper)

#     def merge(self, new_papers: Self):
#         for paper in new_papers.paper_list:
#             self.add(paper)

#     def load_excel(self, filename):
#         df = pd.read_excel(filename, dtype={"idx": str, "status": int})
#         df.fillna("", inplace=True)
#         self.df_to_paper(df)

#     def load_csv(self, filename):
#         df = pd.read_csv(filename, sep=",")
#         self.df_to_paper(df)

#     def clean_missing_author(self):
#         new_paper_list = [paper for paper in self.paper_list if paper.authors != ""]
#         self.paper_list = new_paper_list

#     def df_to_paper(self, df):
#         for idx, row in df.iterrows():
#             paper = PaperRecord(row["email_title"], row["link"])
#             paper.title = row["title"]
#             paper.data = row["data"]
#             paper.authors = row["authors"]
#             paper.idx = row["idx"]
#             paper.year = row["year"] if row["year"] != "" else None
#             paper.status = int(row["status"])
#             paper.created_at = row["created_at"] if row["created_at"] != "" else None
#             paper.updated_at = row["updated_at"] if row["updated_at"] != "" else None
#             self.paper_list.append(paper)

#     def to_dataframe(self):
#         records = []
#         for paper in self.paper_list:
#             records.append(
#                 [
#                     paper.title,
#                     paper.authors,
#                     paper.email_title,
#                     paper.link,
#                     paper.tldr,
#                     paper.year if paper.year is not None else "",
#                     paper.idx,
#                     paper.data,
#                     paper.status,
#                     paper.created_at if paper.created_at is not None else "",
#                     paper.updated_at if paper.updated_at is not None else "",
#                 ]
#             )
#         df = pd.DataFrame(
#             records,
#             columns=[
#                 "title",
#                 "authors",
#                 "email_title",
#                 "link",
#                 "tldr",
#                 "year",
#                 "idx",
#                 "data",
#                 "status",
#                 "created_at",
#                 "updated_at",
#             ],
#         )
#         df = df.astype({"status": "object"})
#         df.fillna("", inplace=True)
#         return df

#     def remove(self, paper):
#         try:
#             idx = self.paper_list.index(paper)
#             self.paper_list.pop(idx)
#         except ValueError:  # Paper not in the list
#             pass
