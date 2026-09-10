'use server'

import { queryWithReden } from '../../lib/db'

export type FraktionStat = {
    fraktion: string | null
    reden: number
    woerter: number
}

export type MonatStat = {
    monat: string
    reden: number
}

export type RedenSummary = {
    reden: number
    woerter: number
    redner: number
    wahlperioden: number[]
}

export type RedenData = {
    summary: RedenSummary
    byFraktion: FraktionStat[]
    byMonat: MonatStat[]
}

export async function loadReden(wahlperiode?: number): Promise<RedenData> {
    const wpFilter = wahlperiode ? 'AND wahlperiode = ?' : ''
    const params = wahlperiode ? [wahlperiode] : []

    const [summaryRows, byFraktion, byMonat, wahlperioden] = await Promise.all([
        queryWithReden<{ reden: number; woerter: number; redner: number }>(
            `SELECT COUNT(DISTINCT rede_id) AS reden,
                    SUM(wortanzahl) AS woerter,
                    COUNT(DISTINCT redner_id) AS redner
             FROM reden.rede
             WHERE NOT ist_praesidium ${wpFilter}`,
            params
        ),
        queryWithReden<FraktionStat>(
            `SELECT fraktion, COUNT(DISTINCT rede_id) AS reden, SUM(wortanzahl) AS woerter
             FROM reden.rede
             WHERE NOT ist_praesidium ${wpFilter}
             GROUP BY fraktion
             ORDER BY reden DESC`,
            params
        ),
        queryWithReden<MonatStat>(
            `SELECT strftime(datum, '%Y-%m') AS monat, COUNT(DISTINCT rede_id) AS reden
             FROM reden.rede
             WHERE NOT ist_praesidium ${wpFilter}
             GROUP BY monat
             ORDER BY monat`,
            params
        ),
        queryWithReden<{ wahlperiode: number }>(
            `SELECT DISTINCT wahlperiode FROM reden.rede ORDER BY wahlperiode`
        ),
    ])

    const summaryRow = summaryRows[0]
    return {
        summary: {
            reden: Number(summaryRow?.reden ?? 0),
            woerter: Number(summaryRow?.woerter ?? 0),
            redner: Number(summaryRow?.redner ?? 0),
            wahlperioden: wahlperioden.map(w => Number(w.wahlperiode)),
        },
        byFraktion: byFraktion.map(row => ({
            fraktion: row.fraktion,
            reden: Number(row.reden),
            woerter: Number(row.woerter ?? 0),
        })),
        byMonat: byMonat.map(row => ({
            monat: row.monat,
            reden: Number(row.reden),
        })),
    }
}
