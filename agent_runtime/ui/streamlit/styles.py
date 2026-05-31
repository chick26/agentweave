from __future__ import annotations

import streamlit as st


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .aw-diagnostic-overview {
            margin: 0.45rem 0 1rem 0;
        }
        .aw-overview-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
            margin: 0 0 0.55rem 0;
        }
        .aw-overview-title {
            font-size: 0.92rem;
            font-weight: 700;
            color: #1f2937;
            line-height: 1.2;
        }
        .aw-overview-subtitle {
            font-size: 0.78rem;
            color: #64748b;
            white-space: nowrap;
        }
        .aw-metric-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.65rem;
        }
        .aw-metric-card {
            min-height: 5.35rem;
            padding: 0.78rem 0.88rem;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            background: #ffffff;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
            overflow: hidden;
        }
        .aw-metric-card.primary {
            border-color: #bfdbfe;
            background: linear-gradient(180deg, #eff6ff 0%, #ffffff 78%);
        }
        .aw-metric-card.ok {
            border-color: #bbf7d0;
            background: linear-gradient(180deg, #f0fdf4 0%, #ffffff 78%);
        }
        .aw-metric-card.warn {
            border-color: #fde68a;
            background: linear-gradient(180deg, #fffbeb 0%, #ffffff 78%);
        }
        .aw-metric-label {
            font-size: 0.75rem;
            color: #64748b;
            font-weight: 650;
            line-height: 1.15;
            margin-bottom: 0.45rem;
        }
        .aw-metric-value {
            font-size: 1.62rem;
            line-height: 1.1;
            font-weight: 760;
            color: #243b5a;
            letter-spacing: 0;
            overflow-wrap: anywhere;
        }
        .aw-metric-detail {
            margin-top: 0.42rem;
            font-size: 0.74rem;
            color: #64748b;
            line-height: 1.25;
            overflow-wrap: anywhere;
        }
        .aw-result-strip {
            display: flex;
            align-items: flex-start;
            gap: 0.48rem;
            margin-top: 0.68rem;
            padding: 0.62rem 0.72rem;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            background: #f8fafc;
        }
        .aw-result-label {
            flex: 0 0 auto;
            font-size: 0.76rem;
            font-weight: 700;
            color: #475569;
            padding-top: 0.06rem;
        }
        .aw-result-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            min-width: 0;
        }
        .aw-chip {
            display: inline-flex;
            align-items: center;
            max-width: 100%;
            padding: 0.16rem 0.48rem;
            border-radius: 999px;
            border: 1px solid #dbeafe;
            background: #eff6ff;
            color: #1e3a8a;
            font-size: 0.74rem;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            overflow-wrap: anywhere;
        }
        @media (max-width: 980px) {
            .aw-metric-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .aw-overview-header {
                align-items: flex-start;
                flex-direction: column;
            }
            .aw-overview-subtitle {
                white-space: normal;
            }
        }
        @media (max-width: 560px) {
            .aw-metric-grid {
                grid-template-columns: minmax(0, 1fr);
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
