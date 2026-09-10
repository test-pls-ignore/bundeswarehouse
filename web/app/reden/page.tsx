'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { loadReden, type RedenData } from './actions'

const FRAKTION_COLORS: Record<string, string> = {
    'CDU/CSU': 'bg-gray-700',
    'SPD': 'bg-red-600',
    'BÜNDNIS 90/DIE GRÜNEN': 'bg-green-600',
    'AfD': 'bg-blue-800',
    'FDP': 'bg-yellow-500',
    'DIE LINKE': 'bg-pink-600',
    'Die Linke': 'bg-pink-600',
    'BSW': 'bg-purple-700',
    'fraktionslos': 'bg-gray-400',
    'Fraktionslos': 'bg-gray-400',
}

function fraktionColor(fraktion: string | null): string {
    if (!fraktion) return 'bg-indigo-400'
    return FRAKTION_COLORS[fraktion] ?? 'bg-indigo-400'
}

export default function RedenPage() {
    const [wahlperiode, setWahlperiode] = useState<number | undefined>(undefined)
    const [data, setData] = useState<RedenData | null>(null)
    const [loading, setLoading] = useState(false)

    useEffect(() => {
        const run = async () => {
            setLoading(true)
            try {
                setData(await loadReden(wahlperiode))
            } finally {
                setLoading(false)
            }
        }
        run()
    }, [wahlperiode])

    const maxReden = data ? Math.max(1, ...data.byFraktion.map(f => f.reden)) : 1
    const maxMonatReden = data ? Math.max(1, ...data.byMonat.map(m => m.reden)) : 1

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col items-center p-8">
            <header className="mb-10 text-center">
                <h1 className="text-4xl font-extrabold text-gray-900 mb-2">Reden nach Fraktion</h1>
                <p className="text-gray-600">Strukturierte Plenarreden (WP ≥ 19), segmentiert nach Sprecher</p>
                <div className="mt-3 flex gap-4 justify-center text-sm">
                    <Link href="/" className="text-blue-600 hover:underline">← Suche</Link>
                    <Link href="/analytics" className="text-emerald-600 hover:underline">Analytics →</Link>
                    <Link href="/ask" className="text-indigo-600 hover:underline">Ask →</Link>
                </div>
            </header>

            {data && data.summary.wahlperioden.length > 1 && (
                <div className="w-full max-w-5xl mb-6 flex items-center justify-end gap-2">
                    <label className="text-sm text-gray-500" htmlFor="reden-wp">Wahlperiode</label>
                    <select
                        id="reden-wp"
                        value={wahlperiode ?? ''}
                        onChange={(e) => setWahlperiode(e.target.value ? Number(e.target.value) : undefined)}
                        className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700"
                    >
                        <option value="">Alle</option>
                        {data.summary.wahlperioden.map(wp => (
                            <option key={wp} value={wp}>WP {wp}</option>
                        ))}
                    </select>
                </div>
            )}

            {loading && <div className="text-gray-500">Lade Reden…</div>}

            {data && !loading && (
                <div className="w-full max-w-5xl space-y-6">
                    <div className="grid grid-cols-3 gap-4">
                        <Kpi label="Reden" value={data.summary.reden.toLocaleString('de-DE')} />
                        <Kpi label="Wörter" value={data.summary.woerter.toLocaleString('de-DE')} />
                        <Kpi label="Redner:innen" value={data.summary.redner.toLocaleString('de-DE')} />
                    </div>

                    <Section title="Reden pro Fraktion">
                        <div className="space-y-2.5">
                            {data.byFraktion.map((row) => (
                                <div key={row.fraktion ?? '(ohne Fraktion)'} className="flex items-center gap-3">
                                    <div className="w-48 shrink-0 text-sm text-gray-700 truncate" title={row.fraktion ?? undefined}>
                                        {row.fraktion ?? 'ohne Fraktion (Regierung u. a.)'}
                                    </div>
                                    <div className="flex-1 bg-gray-100 rounded-full h-4 overflow-hidden">
                                        <div
                                            className={`h-full rounded-full ${fraktionColor(row.fraktion)}`}
                                            style={{ width: `${Math.max(2, (row.reden / maxReden) * 100)}%` }}
                                        />
                                    </div>
                                    <div className="w-20 shrink-0 text-sm text-right text-gray-500 tabular-nums">
                                        {row.reden.toLocaleString('de-DE')}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </Section>

                    <Section title="Reden pro Monat">
                        <div className="overflow-x-auto">
                            <div className="flex items-end gap-1 h-32 min-w-full">
                                {data.byMonat.map((row) => (
                                    <div key={row.monat} className="flex-1 min-w-[6px] flex flex-col items-center justify-end h-full group relative">
                                        <div
                                            className="w-full bg-indigo-400 rounded-t"
                                            style={{ height: `${Math.max(2, (row.reden / maxMonatReden) * 100)}%` }}
                                        />
                                        <div className="absolute -top-6 hidden group-hover:block text-xs bg-gray-800 text-white px-1.5 py-0.5 rounded whitespace-nowrap">
                                            {row.monat}: {row.reden}
                                        </div>
                                    </div>
                                ))}
                            </div>
                            <div className="flex justify-between text-xs text-gray-400 mt-1">
                                <span>{data.byMonat[0]?.monat}</span>
                                <span>{data.byMonat[data.byMonat.length - 1]?.monat}</span>
                            </div>
                        </div>
                    </Section>
                </div>
            )}
        </div>
    )
}

function Kpi({ label, value }: { label: string; value: string }) {
    return (
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 text-center">
            <div className="text-2xl font-bold text-gray-900">{value}</div>
            <div className="text-sm text-gray-500 mt-1">{label}</div>
        </div>
    )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
            <h2 className="text-lg font-bold text-gray-800 mb-4">{title}</h2>
            {children}
        </section>
    )
}
