'use server'

import { query } from '../lib/db'

const PAGE_SIZE = 50

export async function searchBundestag(searchQuery: string) {
    if (!searchQuery) return {
        vorgaenge: [], dokumente: [], aktivitaeten: [],
        counts: { vorgaenge: 0, dokumente: 0, aktivitaeten: 0 }
    }

    const pattern = `%${searchQuery}%`

    const [vorgaenge, dokumente, aktivitaeten, countV, countD, countA] = await Promise.all([
        query(
            `SELECT id, vorgangstyp, titel, datum::VARCHAR AS datum
             FROM vorgang WHERE titel ILIKE ? LIMIT ${PAGE_SIZE}`,
            [pattern]
        ),
        query(
            `SELECT id, drucksachetyp, dokumentnummer, titel, pdf_url
             FROM drucksache WHERE titel ILIKE ? LIMIT ${PAGE_SIZE}`,
            [pattern]
        ),
        query(
            `SELECT id, aktivitaetsart, person_name, datum::VARCHAR AS datum
             FROM aktivitaet WHERE person_name ILIKE ? LIMIT ${PAGE_SIZE}`,
            [pattern]
        ),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM vorgang WHERE titel ILIKE ?`, [pattern]),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM drucksache WHERE titel ILIKE ?`, [pattern]),
        query<{ n: number }>(`SELECT COUNT(*) AS n FROM aktivitaet WHERE person_name ILIKE ?`, [pattern]),
    ])

    return {
        vorgaenge,
        dokumente,
        aktivitaeten,
        counts: {
            vorgaenge: Number(countV[0]?.n ?? 0),
            dokumente: Number(countD[0]?.n ?? 0),
            aktivitaeten: Number(countA[0]?.n ?? 0),
        }
    }
}

export async function loadMore(
    category: 'vorgaenge' | 'dokumente' | 'aktivitaeten',
    searchQuery: string,
    offset: number
) {
    const pattern = `%${searchQuery}%`

    if (category === 'vorgaenge') {
        return query(
            `SELECT id, vorgangstyp, titel, datum::VARCHAR AS datum
             FROM vorgang WHERE titel ILIKE ? LIMIT ${PAGE_SIZE} OFFSET ?`,
            [pattern, offset]
        )
    }
    if (category === 'dokumente') {
        return query(
            `SELECT id, drucksachetyp, dokumentnummer, titel, pdf_url
             FROM drucksache WHERE titel ILIKE ? LIMIT ${PAGE_SIZE} OFFSET ?`,
            [pattern, offset]
        )
    }
    return query(
        `SELECT id, aktivitaetsart, person_name, datum::VARCHAR AS datum
         FROM aktivitaet WHERE person_name ILIKE ? LIMIT ${PAGE_SIZE} OFFSET ?`,
        [pattern, offset]
    )
}
