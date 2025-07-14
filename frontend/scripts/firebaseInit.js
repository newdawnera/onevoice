import { initializeApp } from "https://www.gstatic.com/firebasejs/11.9.1/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/11.9.1/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/11.9.1/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "AIzaSyAom0-UPBlHiwBz5LoIum1pQg0z8vRE9ZQ",
  authDomain: "alliance-2025.firebaseapp.com",
  projectId: "alliance-2025",
  storageBucket: "alliance-2025.firebasestorage.app",
  messagingSenderId: "817847968729",
  appId: "1:817847968729:web:90bd52a0a2669a82862fc8",
};

const app = initializeApp(firebaseConfig);

export const auth = getAuth(app);
export const db = getFirestore(app);
