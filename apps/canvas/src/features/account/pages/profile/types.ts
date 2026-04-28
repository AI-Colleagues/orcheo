export interface ProfileUser {
  name: string;
  email: string;
  avatar: string;
  role: string;
  joinDate?: string;
  twoFactorEnabled: boolean;
}
