package com.example.notes.detail

@AndroidEntryPoint
class NoteDetailActivity : AppCompatActivity() {
    private val viewModel: NoteDetailViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_note_detail)
        viewModel.load(intent.getLongExtra("id", 0L))
    }
}
