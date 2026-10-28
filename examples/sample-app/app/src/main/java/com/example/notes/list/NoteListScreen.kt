package com.example.notes.list

/** Note list — stateless content below, the ViewModel is bound only here. */
@Composable
fun NoteListScreen(viewModel: NoteListViewModel, onOpen: (Long) -> Unit) {
    val state by viewModel.state.collectAsState()
    NoteListContent(state = state, onOpen = onOpen)
}

@Composable
private fun NoteListContent(state: NoteListState, onOpen: (Long) -> Unit) {
    LazyColumn {
        items(state.notes, key = { it.id }) { note ->
            Text(note.title, modifier = Modifier.clickable { onOpen(note.id) })
        }
    }
}
