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

type VorgangItem = {
    id: string
    vorgangstyp: string
    titel: string
    datum: string | null
}

type DokumentItem = {
    id: string
    drucksachetyp: string
    dokumentnummer: string
    titel: string
    pdf_url: string | null
}

type AktivitaetItem = {
    id: string
    aktivitaetsart: string
    person_name: string
    datum: string | null
}

type ContentSearchResult = {
    matches: ContentMatch[]
    ragUnavailable: boolean
}

type SearchResults = {
    vorgaenge: VorgangItem[]
    dokumente: DokumentItem[]
    aktivitaeten: AktivitaetItem[]
    contentMatches: ContentMatch[]
    ragUnavailable: boolean
    counts: { vorgaenge: number; dokumente: number; aktivitaeten: number; contentMatches: number }
}

export async function searchBundestag(searchQuery: string, wahlperiode?: number): Promise<SearchResults> {
    if (!searchQuery) return {
        vorgaenge: [], dokumente: [], aktivitaeten: [],
        contentMatches: [],
        ragUnavailable: false,
        counts: { vorgaenge: 0, dokumente: 0, aktivitaeten: 0, contentMatches: 0 }
    }

    const pattern = `%${searchQuery}%`
    const vWhere = wahlperiode ? ' AND wahlperiode = ?' : ''
    const vParams = wahlperiode ? [pattern, wahlperiode] : [pattern]
    const contentSearchPromise = searchContentMatches(searchQuery, wahlperiode)

    const [vorgaenge, dokumente, aktivitaeten, countV, countD, countA, contentResult] = await Promise.all([
        query<VorgangItem>(
            `SELECT id, vorgangstyp, titel, datum::VARCHAR AS datum
             FROM vorgang WHERE titel ILIKE ?${vWhere} LIMIT ${PAGE_SIZE}`,
            vParams
        ),
        query<DokumentItem>(
            `SELECT id, drucksachetyp, dokumentnummer, titel, pdf_url
             FROM drucksache WHERE titel ILIKE ?${vWhere} LIMIT ${PAGE_SIZE}`,
            vParams
        ),
        query<AktivitaetItem>(
            `SELECT id, aktivitaetsart, person_name, datum::VARCHAR AS datum
             FROM aktivitaet WHERE person_name ILIKE ?${vWhere} LIMIT ${PAGE_SIZE}`,
            vParams
        ),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM vorgang WHERE titel ILIKE ?${vWhere}`, vParams),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM drucksache WHERE titel ILIKE ?${vWhere}`, vParams),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM aktivitaet WHERE person_name ILIKE ?${vWhere}`, vParams),
        contentSearchPromise,
    ])

    return {
        vorgaenge,
        dokumente,
        aktivitaeten,
        contentMatches: contentResult.matches,
        ragUnavailable: contentResult.ragUnavailable,
        counts: {
            vorgaenge: Number(countV[0]?.n ?? 0),
            dokumente: Number(countD[0]?.n ?? 0),
            aktivitaeten: Number(countA[0]?.n ?? 0),
            contentMatches: contentResult.matches.length,
        }
    }
}

async function searchContentMatches(searchQuery: string, wahlperiode?: number): Promise<ContentSearchResult> {
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
        if (!res.ok) {
            return {
                matches: [],
                ragUnavailable: res.status === 503,
            }
        }
        const data = await res.json() as { sources?: ContentMatch[] }
        return {
            matches: data.sources ?? [],
            ragUnavailable: false,
        }
    } catch {
        return {
            matches: [],
            ragUnavailable: true,
        }
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
