'use server'

import { query } from '../../lib/db'

const PAGE_SIZE = 50

export async function browsePlenarprotokolle({
    wahlperiode,
    year,
    specialOnly,
    offset = 0,
}: {
    wahlperiode?: number
    year?: number
    specialOnly?: boolean
    offset?: number
}) {
    const conditions: string[] = []
    const params: unknown[] = []

    if (wahlperiode) {
        conditions.push('wahlperiode = ?')
        params.push(wahlperiode)
    }
    if (year) {
        conditions.push('YEAR(datum) = ?')
        params.push(year)
    }
    if (specialOnly) {
        conditions.push('sitzungsbemerkung IS NOT NULL')
    }

    const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : ''

    const [rows, countResult] = await Promise.all([
        query(
            `SELECT id, dokumentnummer, wahlperiode, datum::VARCHAR AS datum, sitzungsbemerkung, pdf_url
             FROM plenarprotokoll ${where}
             ORDER BY datum DESC NULLS LAST
             LIMIT ${PAGE_SIZE} OFFSET ?`,
            [...params, offset]
        ),
        query<{ n: number }>(
            `SELECT COUNT(*) AS n FROM plenarprotokoll ${where}`,
            params
        ),
    ])

    return {
        rows,
        total: Number(countResult[0]?.n ?? 0),
    }
}
