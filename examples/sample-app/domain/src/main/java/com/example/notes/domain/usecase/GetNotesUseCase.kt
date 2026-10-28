package com.example.notes.domain.usecase

import com.example.notes.domain.repository.NoteRepository

class GetNotesUseCase @Inject constructor(private val repo: NoteRepository) {
    suspend operator fun invoke() = repo.notes()
}
