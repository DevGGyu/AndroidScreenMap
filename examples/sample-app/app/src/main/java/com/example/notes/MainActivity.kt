package com.example.notes

import com.example.notes.detail.NoteDetailActivity
import com.example.notes.list.ListFragment
import com.example.notes.list.NoteListScreen
import com.example.notes.list.NoteListViewModel

@AndroidEntryPoint
class MainActivity : AppCompatActivity() {
    private val viewModel: NoteListViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            NoteListScreen(viewModel = viewModel, onOpen = ::openDetail)
        }
        supportFragmentManager.beginTransaction().add(android.R.id.content, ListFragment()).commit()
    }

    private fun openDetail(id: Long) {
        startActivity(Intent(this, NoteDetailActivity::class.java).putExtra("id", id))
    }
}
