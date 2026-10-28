package com.example.notes.list

import com.example.notes.domain.usecase.GetNotesUseCase

data class NoteListState(val notes: List<Note> = emptyList())

@HiltViewModel
class NoteListViewModel @Inject constructor(
    private val getNotes: GetNotesUseCase,
) : ViewModel() {
    private val _state = MutableStateFlow(NoteListState())
    val state: StateFlow<NoteListState> = _state

    init {
        viewModelScope.launch { _state.value = NoteListState(getNotes()) }
    }
}
