package com.example.notes.data.api

object NotePaths {
    const val BASE = "api/v1"
    const val NOTES = "$BASE/notes"
    const val NOTE = "$BASE/notes/{id}"
}

interface NoteApi {
    @GET(NotePaths.NOTES)
    suspend fun list(): Response<List<NoteDto>>

    @GET(NotePaths.NOTE)
    suspend fun get(@Path("id") id: Long): Response<NoteDto>

    @POST(NotePaths.NOTES)
    suspend fun create(@Body body: NoteDto): Response<NoteDto>
}
