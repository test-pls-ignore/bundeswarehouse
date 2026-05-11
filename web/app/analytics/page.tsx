'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { loadAnalytics, summarizeAnalytics, type AnalyticsData } from './actions'

export default function AnalyticsPage() {
    const [wahlperiode, setWahlperiode] = useState<number>(20)
    const [data, setData] = useState<AnalyticsData | null>(null)
    const [summary, setSummary] = useState<string | null>(null)
    const [loading, setLoading] = useState(false)
    const [summarizing, setSummarizing] = useState(false)

    useEffect(() => {
        const run = async () => {
            setLoading(true)
            setSummary(null)
            try {
                setData(await loadAnalytics(wahlperiode))
            } finally {
                setLoading(false)
            }
        }
        run()
    }, [wahlperiode])

    const onSummarize = async () => {
        setSummarizing(true)
        try {
            setSummary(await summarizeAnalytics(wahlperiode))
        } finally {
            setSummarizing(false)
        }
    }

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col items-center p-8">
            <header className="mb-10 text-center">
                <h1 className="text-4xl font-extrabold text-gray-900 mb-2">Bundestag Analytics</h1>
                <p className="text-gray-600">Vorkonfigurierte Analysen pro Wahlperiode</p>
                <div className="mt-3 flex gap-4 justify-center text-sm">
                    <Link href="/" className="text-blue-600 hover:underline">← Suche</Link>
                    <Link href="/ask" className="text-indigo-600 hover:underline">Ask →</Link>
                </div>
            </header>

            <div className="w-full max-w-5xl mb-6 flex items-center justify-end gap-2">
                <label className="text-sm text-gray-500" htmlFor="analytics-wp">Wahlperiode</label>
                <select
                    id="analytics-wp"
                    value={wahlperiode}
                    onChange={(e) => setWahlperiode(Number(e.target.value))}
                    className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700"
                >
                    <option value={20}>WP 20</option>
                    <option value={19}>WP 19</option>
                </select>
            </div>

            {loading && <div className="text-gray-500">Lade Analysen…</div>}

            {data && !loading && (
                <div className="w-full max-w-5xl space-y-6">
                    <Section title="Dokumentvolumen über Zeit">
                        <SimpleTable rows={data.docVolumeByMonth.map((row) => [row.month, row.n.toLocaleString('de-DE')])} />
                    </Section>

                    <Section title="Top-Themen/Schlagwörter (aus Titeln)">
                        <SimpleTable rows={data.topThemes.map((row) => [row.label, row.n.toLocaleString('de-DE')])} />
                    </Section>

                    <Section title="Aktivitäts-/Typ-Verteilungen">
                        <div className="grid sm:grid-cols-2 gap-4">
                            <Card title="Aktivitätsarten">
                                <SimpleTable rows={data.activityTypes.map((row) => [row.label, row.n.toLocaleString('de-DE')])} />
                            </Card>
                            <Card title="Dokumenttypen">
                                <SimpleTable rows={data.documentTypes.map((row) => [row.label, row.n.toLocaleString('de-DE')])} />
                            </Card>
                        </div>
                    </Section>

                    <Section title="Optional: Analyse zusammenfassen">
                        <button
                            onClick={onSummarize}
                            disabled={summarizing}
                            className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-indigo-700 disabled:opacity-50"
                        >
                            {summarizing ? 'Erstelle Zusammenfassung…' : 'Zusammenfassung erzeugen'}
                        </button>
                        {summary && (
                            <div className="mt-3 text-sm leading-relaxed text-gray-700 whitespace-pre-wrap">{summary}</div>
                        )}
                    </Section>
                </div>
            )}
        </div>
    )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
            <h2 className="text-lg font-bold text-gray-800 mb-3">{title}</h2>
            {children}
        </section>
    )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div className="bg-gray-50 rounded-lg border border-gray-200 p-3">
            <h3 className="text-sm font-semibold text-gray-700 mb-2">{title}</h3>
            {children}
        </div>
    )
}

function SimpleTable({ rows }: { rows: [string, string][] }) {
    if (rows.length === 0) return <div className="text-sm text-gray-500">Keine Daten</div>
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-sm">
                <tbody>
                    {rows.map(([label, value]) => (
                        <tr key={label} className="border-b border-gray-100 last:border-b-0">
                            <td className="py-1.5 text-gray-700">{label}</td>
                            <td className="py-1.5 text-right text-gray-500">{value}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
