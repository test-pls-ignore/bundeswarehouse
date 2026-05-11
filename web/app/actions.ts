'use server'

import { query } from '../lib/db'

const PAGE_SIZE = 50
const RAG_API_URL = process.env.RAG_API_URL ?? 'http://localhost:8000'

export type ContentMatch = {
    chunk_id: string
    doc_id: string
    source_type: 'plenarprotokoll' | 'drucksache'
    speaker: string | null
    titel: string | null
    datum: string
    wahlperiode: number
    pdf_url: string | null
    score: number
    snippet: string
}

export async function searchBundestag(searchQuery: string, wahlperiode?: number) {
    if (!searchQuery) return {
        vorgaenge: [], dokumente: [], aktivitaeten: [],
        contentMatches: [],
        counts: { vorgaenge: 0, dokumente: 0, aktivitaeten: 0, contentMatches: 0 }
    }

    const pattern = `%${searchQuery}%`
    const vWhere = wahlperiode ? ' AND wahlperiode = ?' : ''
    const vParams = wahlperiode ? [pattern, wahlperiode] : [pattern]

    const [vorgaenge, dokumente, aktivitaeten, countV, countD, countA, contentMatches] = await Promise.all([
        query(
            `SELECT id, vorgangstyp, titel, datum::VARCHAR AS datum
             FROM vorgang WHERE titel ILIKE ?${vWhere} LIMIT ${PAGE_SIZE}`,
            vParams
        ),
        query(
            `SELECT id, drucksachetyp, dokumentnummer, titel, pdf_url
             FROM drucksache WHERE titel ILIKE ?${vWhere} LIMIT ${PAGE_SIZE}`,
            vParams
        ),
        query(
            `SELECT id, aktivitaetsart, person_name, datum::VARCHAR AS datum
             FROM aktivitaet WHERE person_name ILIKE ?${vWhere} LIMIT ${PAGE_SIZE}`,
            vParams
        ),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM vorgang WHERE titel ILIKE ?${vWhere}`, vParams),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM drucksache WHERE titel ILIKE ?${vWhere}`, vParams),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM aktivitaet WHERE person_name ILIKE ?${vWhere}`, vParams),
        searchContentMatches(searchQuery, wahlperiode),
    ])

    return {
        vorgaenge,
        dokumente,
        aktivitaeten,
        contentMatches,
        counts: {
            vorgaenge: Number(countV[0]?.n ?? 0),
            dokumente: Number(countD[0]?.n ?? 0),
            aktivitaeten: Number(countA[0]?.n ?? 0),
            contentMatches: contentMatches.length,
        }
    }
}

async function searchContentMatches(searchQuery: string, wahlperiode?: number): Promise<ContentMatch[]> {
    try {
        const res = await fetch(`${RAG_API_URL}/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question: searchQuery,
                wahlperiode: wahlperiode ?? null,
                top_k: 8,
            }),
        })
        if (!res.ok) return []
        const data = await res.json() as { sources?: ContentMatch[] }
        return data.sources ?? []
    } catch {
        return []
    }
}

export async function loadMore(
    category: 'vorgaenge' | 'dokumente' | 'aktivitaeten',
    searchQuery: string,
    offset: number,
    wahlperiode?: number
) {
    const pattern = `%${searchQuery}%`
    const vWhere = wahlperiode ? ' AND wahlperiode = ?' : ''
    const vParams = wahlperiode ? [pattern, wahlperiode, offset] : [pattern, offset]

    if (category === 'vorgaenge') {
        return query(
            `SELECT id, vorgangstyp, titel, datum::VARCHAR AS datum
             FROM vorgang WHERE titel ILIKE ?${vWhere} LIMIT ${PAGE_SIZE} OFFSET ?`,
            vParams
        )
    }
    if (category === 'dokumente') {
        return query(
            `SELECT id, drucksachetyp, dokumentnummer, titel, pdf_url
             FROM drucksache WHERE titel ILIKE ?${vWhere} LIMIT ${PAGE_SIZE} OFFSET ?`,
            vParams
        )
    }
    return query(
        `SELECT id, aktivitaetsart, person_name, datum::VARCHAR AS datum
         FROM aktivitaet WHERE person_name ILIKE ?${vWhere} LIMIT ${PAGE_SIZE} OFFSET ?`,
        vParams
    )
}
