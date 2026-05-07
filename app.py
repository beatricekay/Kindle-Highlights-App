import json
import os
import traceback
import dash
from dash import dcc, html, Input, Output, State
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState
import pandas as pd
from urllib.parse import quote, unquote

WAREHOUSE_ID = "insert-warehouse-id"

BG      = "#13111a"
AMBER   = "#d4a843"
TEXT    = "#ede8f5"
SUBTEXT = "#9090b0"

w = WorkspaceClient()

def run_query(sql_text):
    print(f"[DB] {sql_text[:80].strip()}...")
    response = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql_text,
        wait_timeout="50s"
    )
    if response.status.state != StatementState.SUCCEEDED:
        raise Exception(f"Query failed: {response.status}")
    if not response.result or not response.result.data_array:
        return pd.DataFrame()
    cols = [c.name for c in response.manifest.schema.columns]
    return pd.DataFrame(response.result.data_array, columns=cols)

print("[STARTUP] Loading books...")
BOOKS_DF = pd.DataFrame()
STARTUP_ERROR = None
try:
    BOOKS_DF = run_query("""
        SELECT title, author, cover_url, date_read FROM (
            SELECT title, author, cover_url, MAX(date_read) AS date_read
            FROM projects.kindle.highlights
            WHERE cover_url IS NOT NULL AND cover_url != ''
            GROUP BY title, author, cover_url
        ) ORDER BY date_read DESC NULLS LAST
    """)
    BOOKS_DF["date_read"] = pd.to_datetime(BOOKS_DF["date_read"], errors="coerce")
    print(f"[STARTUP] Loaded {len(BOOKS_DF)} books")
except Exception:
    STARTUP_ERROR = traceback.format_exc()
    print(f"[STARTUP ERROR]\n{STARTUP_ERROR}")

def get_highlights(title):
    safe = title.replace("'", "''")
    return run_query(f"""
        SELECT section, location, page_location, highlight
        FROM projects.kindle.highlights
        WHERE title = '{safe}'
        ORDER BY CAST(location AS BIGINT)
    """)

app = dash.Dash(__name__, suppress_callback_exceptions=True)
server = app.server

app.index_string = """
<!DOCTYPE html>
<html>
<head>
    {%metas%}
    <title>my reading list</title>
    {%favicon%}
    {%css%}
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background-color: #13111a; }
        .book-card img { transition: transform 0.25s ease, box-shadow 0.25s ease; }
        .book-card:hover img {
            transform: translateY(-6px) rotate(-1deg);
            box-shadow: 0 24px 48px rgba(0,0,0,0.6);
        }
        a { color: inherit; text-decoration: none; }
        .back-link:hover { color: #ede8f5 !important; }
        .sort-btn {
            transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
        }
        .sort-btn:hover {
            background-color: rgba(255,255,255,0.08) !important;
            color: #ede8f5 !important;
        }
    </style>
</head>
<body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>
"""

SORT_OPTIONS = [
    ("title_asc",  "A\u2013Z"),
    ("title_desc", "Z\u2013A"),
    ("date_desc",  "Newest"),
    ("date_asc",   "Oldest"),
]

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    dcc.Store(id="sort-order", data="date_desc"),
    html.Div(id="page-content")
], style={"backgroundColor": BG, "minHeight": "100vh",
          "fontFamily": "Georgia, 'Times New Roman', serif"})


def error_page(msg):
    return html.Div([
        html.Pre(msg, style={"color": "#ff6b6b", "fontSize": "13px",
                             "fontFamily": "monospace", "whiteSpace": "pre-wrap"})
    ], style={"padding": "48px"})


def sort_books(df, sort_order):
    if sort_order == "title_asc":
        return df.sort_values("title", ascending=True, key=lambda s: s.str.lower())
    elif sort_order == "title_desc":
        return df.sort_values("title", ascending=False, key=lambda s: s.str.lower())
    elif sort_order == "date_asc":
        return df.sort_values("date_read", ascending=True, na_position="last")
    else:  # date_desc (default)
        return df.sort_values("date_read", ascending=False, na_position="last")


def format_date(date_val, fmt="%b %Y"):
    try:
        d = pd.to_datetime(date_val)
        if pd.isna(d):
            return ""
        return d.strftime(fmt)
    except Exception:
        return ""


def books_page(sort_order="date_desc"):
    if STARTUP_ERROR:
        return error_page(f"Failed to load books:\n\n{STARTUP_ERROR}")
    if BOOKS_DF.empty:
        return error_page("No books found — query returned 0 rows.")

    sorted_df = sort_books(BOOKS_DF, sort_order)

    sort_bar = html.Div([
        html.Span("SORT BY", style={
            "color": SUBTEXT,
            "fontSize": "11px",
            "fontFamily": "monospace",
            "letterSpacing": "1.5px",
            "marginRight": "14px",
            "alignSelf": "center",
        }),
        *[
            html.Button(
                label,
                id={"type": "sort-btn", "index": key},
                n_clicks=0,
                className="sort-btn",
                style={
                    "backgroundColor": "rgba(255,255,255,0.12)" if key == sort_order else "transparent",
                    "color": TEXT if key == sort_order else SUBTEXT,
                    "border": "1px solid rgba(255,255,255,0.25)" if key == sort_order else "1px solid rgba(144,144,176,0.3)",
                    "borderRadius": "20px",
                    "padding": "7px 20px",
                    "fontSize": "13px",
                    "fontFamily": "monospace",
                    "fontWeight": "600" if key == sort_order else "400",
                    "cursor": "pointer",
                    "marginRight": "8px",
                    "outline": "none",
                }
            )
            for key, label in SORT_OPTIONS
        ],
    ], style={
        "display": "flex",
        "alignItems": "center",
        "marginBottom": "40px",
        "flexWrap": "wrap",
        "gap": "4px",
    })

    cards = []
    for _, row in sorted_df.iterrows():
        encoded = quote(str(row["title"]), safe="")
        date_str = format_date(row.get("date_read"))
        cards.append(
            html.Div([
                html.A([
                    html.Img(src=row["cover_url"], style={
                        "width": "100%",
                        "aspectRatio": "2/3",
                        "objectFit": "cover",
                        "borderRadius": "6px",
                        "display": "block",
                    }),
                    html.P(row["title"], style={
                        "marginTop": "12px",
                        "fontSize": "13px",
                        "color": TEXT,
                        "lineHeight": "1.45",
                    }),
                    html.P(date_str, style={
                        "marginTop": "4px",
                        "fontSize": "12px",
                        "color": SUBTEXT,
                        "fontFamily": "monospace",
                    }) if date_str else None,
                ], href=f"/book/{encoded}"),
            ], className="book-card", style={"cursor": "pointer"})
        )

    return html.Div([
        html.H1("what i've been reading", style={
            "color": TEXT,
            "fontSize": "2.4rem",
            "fontWeight": "700",
            "letterSpacing": "-0.5px",
            "marginBottom": "28px",
        }),
        sort_bar,
        html.Div(cards, style={
            "display": "grid",
            "gridTemplateColumns": "repeat(5, 1fr)",
            "gap": "36px 24px",
        })
    ], style={"maxWidth": "1120px", "margin": "0 auto", "padding": "64px 32px"})


def highlights_page(title):
    try:
        highlights = get_highlights(title)
    except Exception:
        return error_page(f"Failed to load highlights:\n\n{traceback.format_exc()}")

    match = BOOKS_DF[BOOKS_DF["title"] == title]
    book = match.iloc[0] if not match.empty else None

    cards = []
    current_section = None
    for _, row in highlights.iterrows():
        section = row.get("section")
        if section and section != current_section:
            current_section = section
            cards.append(html.P(section, style={
                "color": AMBER, "fontSize": "11px", "fontFamily": "monospace",
                "textTransform": "uppercase", "letterSpacing": "2px",
                "margin": "32px 0 12px",
            }))
        cards.append(html.Div([
            html.P(f'"{row["highlight"]}"', style={
                "margin": "0 0 10px 0", "fontSize": "15px",
                "lineHeight": "1.75", "color": TEXT,
            }),
            html.Span(row["page_location"] or "", style={
                "fontSize": "11px", "color": SUBTEXT, "fontFamily": "monospace",
            }),
        ], style={
            "backgroundColor": "rgba(212, 168, 67, 0.07)",
            "borderLeft": f"3px solid {AMBER}",
            "padding": "20px 24px",
            "borderRadius": "0 8px 8px 0",
            "marginBottom": "14px",
        }))

    date_str = format_date(book["date_read"], "%d %b %Y") if book is not None else ""

    return html.Div([
        html.A("← back", href="/", className="back-link", style={
            "color": SUBTEXT, "fontSize": "13px", "fontFamily": "monospace",
            "display": "inline-block", "marginBottom": "52px",
        }),
        html.Img(
            src=book["cover_url"] if book is not None else "",
            style={
                "width": "200px", "borderRadius": "6px",
                "boxShadow": "0 24px 60px rgba(0,0,0,0.55)",
                "display": "block", "margin": "0 auto 32px",
            }
        ),
        html.H1(title, style={
            "color": TEXT, "fontSize": "2rem", "fontWeight": "700",
            "textAlign": "center", "lineHeight": "1.25", "marginBottom": "12px",
        }),
        html.P(book["author"] if book is not None else "", style={
            "color": SUBTEXT, "textAlign": "center",
            "fontFamily": "monospace", "fontSize": "13px", "marginBottom": "8px",
        }),
        html.P(f"Read {date_str}" if date_str else "", style={
            "color": SUBTEXT, "textAlign": "center",
            "fontFamily": "monospace", "fontSize": "12px", "marginBottom": "56px",
        }),
        html.Div(cards),
    ], style={"maxWidth": "760px", "margin": "0 auto", "padding": "60px 32px"})


@app.callback(
    Output("sort-order", "data"),
    Input({"type": "sort-btn", "index": dash.ALL}, "n_clicks"),
    State("sort-order", "data"),
    prevent_initial_call=True,
)
def update_sort(n_clicks_list, current_sort):
    ctx = dash.callback_context
    if not ctx.triggered:
        return current_sort
    triggered_prop = ctx.triggered[0]["prop_id"]
    btn_id = json.loads(triggered_prop.split(".")[0])
    return btn_id["index"]


@app.callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
    Input("sort-order", "data"),
)
def display_page(pathname, sort_order):
    print(f"[CALLBACK] pathname={pathname}, sort={sort_order}")
    if pathname and pathname.startswith("/book/"):
        return highlights_page(unquote(pathname[6:]))
    return books_page(sort_order or "date_desc")


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8050)
