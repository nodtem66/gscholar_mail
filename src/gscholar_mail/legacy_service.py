import base64
import os

import diskcache as dc
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .html_parser import HTMLParser
from .paper_record import PaperRecord

# If modifying these scopes, delete the file token.pickle.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.modify",
]


def get_creds(
    token_json_filename="./token.json",
    client_secrets_filename="./credentials.json",
    scopes=SCOPES,
):
    """Credentials for Google APIs. If there are no (valid) credentials available, let the user log in.
    Args:
    - token_json_filename: The file where the user's access and refresh tokens are stored.
    - client_secrets_filename: The file where the client secrets are stored.
    - scopes: The scopes for which to request access.
    Returns:
    Credentials for Google APIs.
    """
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(token_json_filename):
        creds = Credentials.from_authorized_user_file(token_json_filename, scopes)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secrets_filename, scopes
            )
            flow.authorization_url(prompt="consent")
            creds = flow.run_local_server(
                port=0,
                access_type="offline",
                include_granted_scopes=True,
                enable_granular_consent=True,
            )
    # Save the token for the next run
    with open(token_json_filename, "w") as token:
        token.write(creds.to_json())
    return creds


def get_gmail_service(credentials):
    return build("gmail", "v1", credentials=credentials)


def get_drive_service(credentials):
    return build("drive", "v3", credentials=credentials)


def get_sheets_service(credentials):
    return build("sheets", "v4", credentials=credentials)


class GmailService:
    def __init__(self, credentials):
        self.service = build("gmail", "v1", credentials=credentials)

    def list_emails_with_labels(self, user_id, label_ids=[], maxResults=100) -> list:
        """List all Messages of the user's mailbox with label_ids applied.

        Args:
        - user_id: User's email address. The special value "me"
        can be used to indicate the authenticated user.
        - label_ids: Only return Messages with these labelIds applied.
        - maxResults: Maximum number of messages to return.

        Returns:
        List of Messages that have all required Labels applied. Note that the
        returned list contains Message IDs, you must use get with the
        appropriate id to get the details of a Message.
        """
        try:
            response = (
                self.service.users()
                .messages()
                .list(userId=user_id, labelIds=label_ids)
                .execute()
            )
            messages = []
            if "messages" in response:
                messages.extend(response["messages"])

            while "nextPageToken" in response:
                if maxResults > 0 and len(messages) >= maxResults:
                    break
                page_token = response["nextPageToken"]
                response = (
                    self.service.users()
                    .messages()
                    .list(userId=user_id, labelIds=label_ids, pageToken=page_token)
                    .execute()
                )
                messages.extend(response["messages"])

            return messages
        except Exception as error:
            print("An error occurred: %s" % error)
        return []

    def get_label_ids(self, user_id, label_names=[]):
        results = self.service.users().labels().list(userId=user_id).execute()
        labels = results.get("labels", [])

        label_ids = []
        for name in label_names:
            for label in labels:
                if label["name"] == name:
                    label_ids.append(label["id"])

        return label_ids

    def get_message(self, user_id, msg_id):
        """Get a Message with given ID.

        Args:
        user_id: User's email address. The special value "me"
        can be used to indicate the authenticated user.
        msg_id: The ID of the Message required.

        Returns:
        A Message.
        """
        try:
            message = (
                self.service.users().messages().get(userId=user_id, id=msg_id).execute()
            )
            return message
        except Exception as error:
            print("An error occurred: %s" % error)

    def mark_as_read(self, email_id):
        """Mark an email as read by removing the UNREAD label."""
        try:
            self.service.users().messages().modify(
                userId="me",
                id=email_id,
                body={"addLabelIds": [], "removeLabelIds": ["UNREAD", "INBOX"]},
            ).execute()
        except Exception as error:
            print("An error occurred: %s" % error)

    def process_emails_to_paper_records(
        self, emails, mark_as_read=False
    ) -> list[PaperRecord]:
        """Process a list of email IDs and return their content."""
        paper_record: list[PaperRecord] = []
        paper_idx: dict[str, bool] = {}
        for email in emails:
            mail_content = self.get_message("me", email["id"])
            if (
                mail_content is None
                or "payload" not in mail_content
                or "body" not in mail_content["payload"]
                or "data" not in mail_content["payload"]["body"]
            ):
                continue
            try:
                msg_str = base64.urlsafe_b64decode(
                    mail_content["payload"]["body"]["data"]
                ).decode("utf-8", "ignore")
            except KeyError:
                # If something is wrong with the email content, skip it. Maybe we have to process it again later.
                continue

            mail_title = ""
            for h in mail_content["payload"]["headers"]:
                if h["name"] == "Subject":
                    mail_title = h["value"]
            parser = HTMLParser(mail_title)
            parser.parse(msg_str)
            parser.check_valid_papers()

            for paper in parser.papers:
                # Prevent duplicate papers by checking the index.
                # The index is generated by normalizing the title.
                if paper.idx not in paper_idx:
                    paper_record.append(paper)
                    paper_idx[paper.idx] = True

            # If the email is processed, mark it as read and remove from inbox
            # We will mark it as read after processing all the papers to avoid
            # missing any paper due to unexpected error in the middle of processing.
            if mark_as_read:
                self.mark_as_read(email["id"])
        return paper_record


class SheetService:
    def __init__(self, credentials):
        self.service = build("sheets", "v4", credentials=credentials)

    def create_cache(self, cache_folder, file_id, sheet_name="Sheet1"):
        cache = dc.Cache(cache_folder)
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .get(spreadsheetId=file_id, range=f"'{sheet_name}'")
                .execute()
            )
            values = result.get("values", [])
            num_rows = len(values)
            for i in range(1, num_rows):  # Skip the header row
                row = values[i]
                if len(row) < 7:
                    continue
                idx = row[6]
                cache[idx] = True
        except HttpError as error:
            print(f"An error occurred: {error}")

    def append(self, file_id, record: list[PaperRecord], sheet_name="Sheet1"):
        append_data = []
        for paper in record:
            append_data.append(
                [
                    paper.title,
                    paper.authors,
                    paper.email_title,
                    paper.link,
                    "",  # TLDR is not available at the moment, we will remove it later.
                    paper.year,
                    paper.idx,
                    paper.data,
                    paper.status,
                    paper.created_at,
                    paper.updated_at,
                ]
            )
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=file_id,
                    range=f"'{sheet_name}'",
                    valueInputOption="USER_ENTERED",
                    body={"values": append_data},
                )
                .execute()
            )
            print(f"{(result.get('updates').get('updatedCells'))} cells appended.")
        except HttpError as error:
            print(f"An error occurred: {error}")
