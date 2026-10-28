package com.example.notes.data.source

import com.example.notes.data.api.NoteApi

interface NoteDataSource {
    suspend fun list(): Response<List<NoteDto>>
    suspend fun get(id: Long): Response<NoteDto>
}

class NoteDataSourceImpl @Inject constructor(
    private val api: NoteApi,
) : NoteDataSource {
    override suspend fun list(): Response<List<NoteDto>> = api.list()
    override suspend fun get(id: Long): Response<NoteDto> = api.get(id)
}
