'use server'

import { query } from '../lib/db'

export async function searchBundestag(searchQuery: string) {
    if (!searchQuery) return { vorgaenge: [], dokumente: [], aktivitaeten: [] }

    const pattern = `%${searchQuery}%`

    const [vorgaenge, dokumente, aktivitaeten] = await Promise.all([
        query(
            `SELECT id, vorgangstyp, titel, datum::VARCHAR AS datum
             FROM vorgang WHERE titel ILIKE ? LIMIT 10`,
            [pattern]
        ),
        query(
            `SELECT id, drucksachetyp, dokumentnummer, titel, pdf_url
             FROM drucksache WHERE titel ILIKE ? LIMIT 10`,
            [pattern]
        ),
        query(
            `SELECT id, aktivitaetsart, person_name, datum::VARCHAR AS datum
             FROM aktivitaet WHERE person_name ILIKE ? LIMIT 10`,
            [pattern]
        ),
    ])

    return { vorgaenge, dokumente, aktivitaeten }
}
