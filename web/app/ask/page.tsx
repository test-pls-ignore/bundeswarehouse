'use client'

import { useState } from 'react'
import Link from 'next/link'
import { askRag, type AskResult, type Source } from './actions'

export default function AskPage() {
    const [question, setQuestion] = useState('')
    const [wahlperiode, setWahlperiode] = useState<number>(20)
    const [result, setResult] = useState<AskResult | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()
        if (!question.trim()) return
        setLoading(true)
        setError(null)
        setResult(null)
        try {
            setResult(await askRag(question.trim(), wahlperiode))
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unknown error')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col items-center p-8">
            <header className="mb-12 text-center">
                <h1 className="text-4xl font-extrabold text-gray-900 mb-2">Bundestag Warehouse</h1>
                <p className="text-gray-600">Ask a question — answered from parliamentary documents</p>
                <div className="mt-3 flex gap-4 justify-center text-sm">
                    <Link href="/" className="text-blue-600 hover:underline">← Keyword search</Link>
                    <Link href="/analytics" className="text-emerald-600 hover:underline">Analytics →</Link>
                    <Link href="/plenarprotokoll" className="text-orange-600 hover:underline">Plenarprotokoll-Browser →</Link>
                </div>
            </header>

            <form onSubmit={handleSubmit} className="w-full max-w-2xl mb-8">
                <div className="mb-2 flex items-center justify-end gap-2">
                    <label className="text-sm text-gray-500" htmlFor="ask-wahlperiode-select">Wahlperiode</label>
                    <select
                        id="ask-wahlperiode-select"
                        value={wahlperiode}
                        onChange={e => setWahlperiode(Number(e.target.value))}
                        className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700"
                    >
                        <option value={20}>WP 20</option>
                        <option value={19}>WP 19</option>
                    </select>
                </div>
                <div className="relative">
                    <textarea
                        className="w-full p-4 pl-6 pr-36 rounded-2xl shadow-lg border-2 border-transparent focus:border-indigo-500 focus:outline-none text-lg text-gray-800 resize-none"
                        placeholder="z.B. Was hat der Bundestag zur Klimapolitik beschlossen?"
                        rows={3}
                        value={question}
                        onChange={e => setQuestion(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(e) } }}
                    />
                    <button
                        type="submit"
                        disabled={loading || !question.trim()}
                        className="absolute right-3 bottom-3 bg-indigo-600 text-white px-5 py-2 rounded-xl font-medium hover:bg-indigo-700 transition disabled:opacity-40"
                    >
                        {loading ? 'Thinking…' : 'Ask'}
                    </button>
                </div>
                <p className="text-xs text-gray-400 mt-2 pl-2">Enter to submit · Shift+Enter for new line</p>
            </form>

            {error && (
                <div className="w-full max-w-2xl mb-6 p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
                    {error}
                </div>
            )}

            {result && (
                <div className="w-full max-w-2xl space-y-6">
                    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
                        <div className="text-xs font-semibold text-indigo-500 uppercase tracking-wide mb-3">Answer</div>
                        <div className="text-gray-800 leading-relaxed whitespace-pre-wrap">{result.text}</div>
                    </div>

                    {result.sources.length > 0 && (
                        <div>
                            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
                                Sources ({result.sources.length})
                            </h2>
                            <div className="space-y-3">
                                {result.sources.map((src, i) => (
                                    <SourceCard key={src.chunk_id} index={i + 1} src={src} />
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

function SourceCard({ index, src }: { index: number; src: Source }) {
    const isPlenar = src.source_type === 'plenarprotokoll'
    const accent = isPlenar ? 'text-orange-600' : 'text-purple-600'
    const badge = isPlenar ? 'bg-orange-50 text-orange-700' : 'bg-purple-50 text-purple-700'
    const label = isPlenar ? 'Plenarprotokoll' : src.titel ?? 'Drucksache'
    const sub = isPlenar
        ? (src.speaker ? `Redner: ${src.speaker}` : undefined)
        : (src.doc_id)

    return (
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 flex gap-4">
            <div className="text-2xl font-bold text-gray-200 w-6 shrink-0 text-right">{index}</div>
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${badge}`}>
                        {isPlenar ? 'Plenarprotokoll' : 'Drucksache'}
                    </span>
                    <span className="text-xs text-gray-400">
                        WP {src.wahlperiode} · {src.datum ? new Date(src.datum).toLocaleDateString('de-DE') : ''}
                    </span>
                    <span className="text-xs text-gray-300 ml-auto">score {src.score.toFixed(2)}</span>
                </div>
                <div className={`font-semibold text-sm mb-1 truncate ${accent}`}>{label}</div>
                {sub && <div className="text-xs text-gray-500 mb-2">{sub}</div>}
                <p className="text-sm text-gray-600 line-clamp-3">{src.snippet}</p>
                {src.pdf_url && (
                    <a
                        href={src.pdf_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-block mt-2 text-xs text-gray-400 hover:text-red-500 transition"
                    >
                        PDF ↗
                    </a>
                )}
            </div>
        </div>
    )
}
