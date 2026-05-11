'use server'

import { query } from '@/lib/db'

const RAG_API_URL = process.env.RAG_API_URL ?? 'http://localhost:8000'

export type MonthlyPoint = { month: string; count: number }
export type DistributionPoint = { label: string; count: number }

export type AnalyticsData = {
    wahlperiode: number
    docVolumeByMonth: MonthlyPoint[]
    topThemes: DistributionPoint[]
    activityTypes: DistributionPoint[]
    documentTypes: DistributionPoint[]
}

export async function loadAnalytics(wahlperiode: number): Promise<AnalyticsData> {
    const [docVolumeByMonth, activityTypes, documentTypes, titleRows] = await Promise.all([
        query<MonthlyPoint>(
            `
            SELECT strftime(datum, '%Y-%m') AS month, COUNT(*)::INTEGER AS count
            FROM drucksache
            WHERE wahlperiode = ?
            GROUP BY 1
            ORDER BY 1
            `,
            [wahlperiode]
        ),
        query<{ label: string; count: number }>(
            `
            SELECT COALESCE(aktivitaetsart, 'Unbekannt') AS label, COUNT(*)::INTEGER AS count
            FROM aktivitaet
            WHERE wahlperiode = ?
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 15
            `,
            [wahlperiode]
        ),
        query<{ label: string; count: number }>(
            `
            SELECT COALESCE(drucksachetyp, 'Unbekannt') AS label, COUNT(*)::INTEGER AS count
            FROM drucksache
            WHERE wahlperiode = ?
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 15
            `,
            [wahlperiode]
        ),
        query<{ titel: string | null }>(
            `
            SELECT titel
            FROM drucksache
            WHERE wahlperiode = ? AND titel IS NOT NULL
            LIMIT 5000
            `,
            [wahlperiode]
        ),
    ])

    return {
        wahlperiode,
        docVolumeByMonth,
        topThemes: computeTopThemes(titleRows.map((row) => row.titel ?? '')),
        activityTypes,
        documentTypes,
    }
}

const STOPWORDS = new Set([
    'der', 'die', 'das', 'und', 'oder', 'mit', 'für', 'von', 'des', 'dem', 'den', 'ein', 'eine', 'einer', 'eines',
    'im', 'in', 'am', 'an', 'auf', 'zu', 'zur', 'zum', 'über', 'unter', 'bei', 'nach', 'vor', 'als', 'ist',
    'sowie', 'durch', 'nicht', 'wird', 'werden', 'vom', 'dass', 'aus', 'auch', 'mehr', 'zwischen',
])

function computeTopThemes(titles: string[]): DistributionPoint[] {
    const counts = new Map<string, number>()
    for (const title of titles) {
        for (const token of title.toLowerCase().split(/[^a-zA-Zäöüß]+/g)) {
            if (token.length < 4 || STOPWORDS.has(token)) continue
            counts.set(token, (counts.get(token) ?? 0) + 1)
        }
    }
    return Array.from(counts.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 15)
        .map(([label, count]) => ({ label, count }))
}

export async function summarizeAnalytics(wahlperiode: number): Promise<string | null> {
    const data = await loadAnalytics(wahlperiode)
    const prompt = [
        `Fasse die wichtigsten Erkenntnisse für Wahlperiode ${wahlperiode} zusammen.`,
        `Dokumente je Monat: ${JSON.stringify(data.docVolumeByMonth.slice(-12))}`,
        `Top-Themen: ${JSON.stringify(data.topThemes.slice(0, 10))}`,
        `Aktivitätsarten: ${JSON.stringify(data.activityTypes.slice(0, 10))}`,
        `Dokumenttypen: ${JSON.stringify(data.documentTypes.slice(0, 10))}`,
    ].join('\n')

    try {
        const res = await fetch(`${RAG_API_URL}/ask`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: prompt, wahlperiode }),
        })
        if (!res.ok) return null
        const body = await res.json() as { text?: string }
        return body.text ?? null
    } catch {
        return null
    }
}
