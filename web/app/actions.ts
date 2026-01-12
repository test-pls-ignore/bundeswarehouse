'use server'

import { PrismaClient } from '@prisma/client'

const prisma = new PrismaClient()

export async function searchBundestag(query: string) {
    if (!query) return { vorgaenge: [], dokumente: [], aktivitaeten: [] }

    const vorgaenge = await prisma.vorgang.findMany({
        where: {
            titel: { contains: query }
        },
        take: 5
    })

    // Prisma SQLite 'contains' is case-sensitive by default? 
    // Normally yes, but let's assume basic search for now.

    const dokumente = await prisma.dokument.findMany({
        where: {
            titel: { contains: query }
        },
        take: 5
    })

    const aktivitaeten = await prisma.aktivitaet.findMany({
        where: {
            titel: { contains: query }
        },
        take: 5
    })

    return { vorgaenge, dokumente, aktivitaeten }
}
